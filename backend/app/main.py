from __future__ import annotations

import asyncio
import dataclasses
import shutil
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import httpx

from .config import settings
from .db import ActiveJobConflict, Database
from .events import event_hub
from .services.platforms import platform_registry
from .services.agent_runner import (
    capture_job_snapshot,
    close_preserved_sessions,
    finish_job_intervention,
    focus_job_browser,
    pause_job,
    resume_job,
    start_job,
    stop_job,
)
from .services.notifier import send_completion_notification
from .services import weixin_connector
from .services.preview import preview_manager


settings.ensure_directories()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.FileHandler(settings.logs_root / "backend.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("answer-studio")
database = Database(settings)
_collection_locks: dict[str, asyncio.Lock] = {}


def collection_lock(account_id: str) -> asyncio.Lock:
    lock = _collection_locks.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _collection_locks[account_id] = lock
    return lock


def collection_due(account: dict[str, Any]) -> bool:
    if not account.get("auto_collect_enabled"):
        return False
    last_collection = account.get("last_collection_at")
    if not last_collection:
        return True
    try:
        last = datetime.fromisoformat(last_collection)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    interval = max(30, int(account.get("auto_collect_interval_minutes") or 360))
    return datetime.now(timezone.utc) - last >= timedelta(minutes=interval)


def current_academic_year() -> int:
    today = datetime.now()
    return today.year if today.month >= 8 else today.year - 1


async def run_collection_scheduler() -> None:
    await asyncio.sleep(15)
    while True:
        for account in database.list_accounts():
            if (
                account["status"] != "ready"
                or not collection_due(account)
                or database.has_active_job(account["id"])
                or await preview_manager.is_interactive(account["id"])
            ):
                continue
            lock = collection_lock(account["id"])
            if lock.locked():
                continue
            try:
                await perform_collection(
                    account["id"],
                    current_academic_year(),
                )
                logger.info(
                    "Scheduled collection completed for account %s",
                    account["id"],
                )
            except Exception:
                logger.exception(
                    "Scheduled collection failed for account %s",
                    account["id"],
                )
        await asyncio.sleep(60)


async def run_job_health_monitor() -> None:
    await asyncio.sleep(15)
    while True:
        for job in database.stale_running_jobs(120):
            event = database.add_event(
                job["id"],
                "stalled",
                "任务超过 2 分钟未收到心跳，可能已卡住",
                {
                    "last_heartbeat_at": job.get("last_heartbeat_at"),
                    "account_name": job.get("account_name"),
                },
            )
            await event_hub.publish(job["id"], event)
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    database.mark_interrupted_jobs()
    if (
        not database.get_setting("model_config", None)
        and settings.openai_api_key
    ):
        database.set_setting(
            "model_config",
            {
                "provider_name": "ssh" if "sshzyu" in settings.openai_base_url else "custom",
                "base_url": settings.openai_base_url,
                "api_key": settings.openai_api_key,
                "models": [
                    settings.openai_model,
                    *(
                        [settings.openai_fallback_model]
                        if settings.openai_fallback_model
                        else []
                    ),
                ],
                "reasoning_effort": settings.reasoning_effort,
            },
        )
    scheduler_task = asyncio.create_task(run_collection_scheduler())
    health_monitor_task = asyncio.create_task(run_job_health_monitor())
    logger.info("Local AI assignment studio started")
    try:
        yield
    finally:
        scheduler_task.cancel()
        health_monitor_task.cancel()
        await asyncio.gather(
            scheduler_task,
            health_monitor_task,
            return_exceptions=True,
        )
        await close_preserved_sessions()
        await preview_manager.close()
        logger.info("Local AI assignment studio stopped")


app = FastAPI(title="Local AI Assignment Studio", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:8877",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateAccountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    platform: str = "chaoxing"


class UpdateAccountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CreateJobRequest(BaseModel):
    assignment_id: str
    auto_submit: bool = False
    interactive: bool = True


class TestWebhookRequest(BaseModel):
    message: str = "本地 AI 作业助手通知测试"


class ModelConfigRequest(BaseModel):
    provider_name: str = "ssh"
    base_url: str = Field(min_length=1)
    api_key: str = ""
    models: list[str] = Field(default_factory=list)
    reasoning_effort: str = "high"


class WeixinStatusRequest(BaseModel):
    code: str
    verify_code: str = ""


class CollectionScheduleRequest(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=30, le=10080)


class SubmissionVerificationRequest(BaseModel):
    status: str
    note: str = Field(default="", max_length=1000)


def get_model_config() -> dict[str, Any]:
    stored = database.get_setting("model_config", {}) or {}
    return {
        "provider_name": stored.get("provider_name", "ssh"),
        "base_url": stored.get("base_url", settings.openai_base_url),
        "api_key": stored.get("api_key", settings.openai_api_key),
        "models": stored.get("models", [settings.openai_model]),
        "reasoning_effort": stored.get(
            "reasoning_effort",
            settings.reasoning_effort,
        ),
    }


def effective_settings() -> Any:
    config = get_model_config()
    weixin = database.get_setting("weixin_binding", {}) or {}
    return dataclasses.replace(
        settings,
        openai_base_url=config["base_url"],
        openai_api_key=config["api_key"],
        openai_model=(config.get("models") or [settings.openai_model])[0],
        openai_fallback_model=(
            (config.get("models") or [settings.openai_model])[1]
            if len(config.get("models") or []) > 1
            else settings.openai_fallback_model
        ),
        reasoning_effort=config.get("reasoning_effort", "high"),
        weixin_bot_token=weixin.get("token", ""),
        weixin_user_id=weixin.get("user_id", ""),
        weixin_context_token=weixin.get("context_token", ""),
        weixin_base_url=weixin.get("base", settings.weixin_base_url),
    )


def ensure_account_idle(account_id: str) -> None:
    if database.has_active_job(account_id):
        raise HTTPException(
            status_code=409,
            detail="该账号有排队或运行中的 Agent 任务，请等待任务结束或先停止任务",
        )


@app.get("/api/health")
async def health() -> dict[str, Any]:
    model_config = get_model_config()
    return {
        "ok": True,
        "version": "0.4.2",
        "data_root": str(settings.data_root),
        "model": (model_config["models"] or [settings.openai_model])[0],
        "ai_configured": bool(model_config["api_key"]),
    }


@app.get("/api/accounts")
async def list_accounts() -> list[dict[str, Any]]:
    return database.list_accounts()


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    config = get_model_config()
    effective = effective_settings()
    return {
        "data_root": str(settings.data_root),
        "logs_root": str(settings.logs_root),
        "provider_name": config["provider_name"],
        "openai_base_url": config["base_url"],
        "openai_model": effective.openai_model,
        "models": config["models"],
        "reasoning_effort": config["reasoning_effort"],
        "ai_configured": bool(config["api_key"]),
        "webhook_configured": bool(settings.webhook_url),
        "weixin_configured": bool(
            effective.weixin_bot_token and effective.weixin_user_id
        ),
        "weixin_ready": bool(effective.weixin_context_token),
    }


@app.get("/api/model-config")
async def get_model_settings() -> dict[str, Any]:
    config = get_model_config()
    return {
        "provider_name": config["provider_name"],
        "base_url": config["base_url"],
        "api_key_configured": bool(config["api_key"]),
        "models": config["models"],
        "reasoning_effort": config["reasoning_effort"],
    }


@app.put("/api/model-config")
async def save_model_settings(payload: ModelConfigRequest) -> dict[str, Any]:
    existing = get_model_config()
    api_key = payload.api_key.strip() or existing["api_key"]
    models = [item.strip() for item in payload.models if item.strip()]
    if not models:
        raise HTTPException(status_code=400, detail="至少添加一个模型")
    reasoning_effort = payload.reasoning_effort.strip().lower()
    if reasoning_effort not in {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    }:
        raise HTTPException(status_code=400, detail="不支持的思考强度")
    database.set_setting(
        "model_config",
        {
            "provider_name": payload.provider_name.strip() or "custom",
            "base_url": payload.base_url.rstrip("/"),
            "api_key": api_key,
            "models": models,
            "reasoning_effort": reasoning_effort,
        },
    )
    return {"ok": True, "models": models}


@app.post("/api/model-config/fetch-models")
async def fetch_provider_models(payload: ModelConfigRequest) -> dict[str, Any]:
    config = get_model_config()
    api_key = payload.api_key.strip() or config["api_key"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{payload.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as error:
        detail = error.response.text.strip()[:500]
        raise HTTPException(
            status_code=400,
            detail=f"服务商拒绝模型列表请求：{detail or error.response.status_code}",
        ) from error
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(
            status_code=400,
            detail=f"无法读取模型列表：{error}",
        ) from error
    return {
        "models": [
            item.get("id")
            for item in data.get("data", [])
            if item.get("id")
        ]
    }


@app.post("/api/model-config/test")
async def test_provider_model(payload: ModelConfigRequest) -> dict[str, Any]:
    config = get_model_config()
    api_key = payload.api_key.strip() or config["api_key"]
    model = (payload.models or config["models"])[0]
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{payload.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "只回复 OK"}],
                    "max_tokens": 8,
                },
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as error:
        detail = error.response.text.strip()[:500]
        raise HTTPException(
            status_code=400,
            detail=f"模型连接失败：{detail or error.response.status_code}",
        ) from error
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=400,
            detail=f"无法连接模型服务：{error}",
        ) from error
    return {"ok": True}


@app.post("/api/connectors/weixin/qrcode")
async def create_weixin_qrcode() -> dict[str, Any]:
    return await weixin_connector.begin_binding(database)


@app.post("/api/connectors/weixin/status")
async def check_weixin_qrcode(payload: WeixinStatusRequest) -> dict[str, Any]:
    return await weixin_connector.check_binding(
        database,
        payload.code,
        payload.verify_code,
    )


@app.post("/api/connectors/weixin/refresh")
async def refresh_weixin_session() -> dict[str, Any]:
    return await weixin_connector.refresh_context(database)


@app.post("/api/accounts")
async def create_account(payload: CreateAccountRequest) -> dict[str, Any]:
    try:
        platform_registry.get(payload.platform)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return database.create_account(payload.name, payload.platform, settings)


@app.patch("/api/accounts/{account_id}")
async def update_account(
    account_id: str,
    payload: UpdateAccountRequest,
) -> dict[str, Any]:
    try:
        database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    database.update_account(account_id, name=payload.name.strip())
    return database.get_account(account_id)


@app.patch("/api/accounts/{account_id}/collection-schedule")
async def update_collection_schedule(
    account_id: str,
    payload: CollectionScheduleRequest,
) -> dict[str, Any]:
    try:
        account = database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    fields = {}
    if payload.enabled is not None:
        fields["auto_collect_enabled"] = int(payload.enabled)
        if payload.enabled and not account.get("last_collection_at"):
            fields["last_collection_at"] = datetime.now(timezone.utc).isoformat()
            fields["last_collection_status"] = "pending"
    if payload.interval_minutes is not None:
        fields["auto_collect_interval_minutes"] = payload.interval_minutes
    if not fields:
        raise HTTPException(status_code=400, detail="没有可更新的自动采集配置")
    database.update_account(account_id, **fields)
    return database.get_account(account_id)


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: str) -> dict[str, Any]:
    try:
        account = database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    ensure_account_idle(account_id)
    await preview_manager.invalidate(account_id)
    database.delete_account(account_id)
    shutil.rmtree(account["profile_dir"], ignore_errors=True)
    return {"ok": True}


@app.post("/api/accounts/{account_id}/login")
async def login_account(account_id: str) -> dict[str, Any]:
    try:
        account = database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    ensure_account_idle(account_id)
    return platform_registry.get(account["platform"]).start_login(
        database, account_id, settings
    )


@app.get("/api/accounts/{account_id}/login-status")
async def get_login_status(account_id: str) -> dict[str, Any]:
    account = database.get_account(account_id)
    return platform_registry.get(account["platform"]).login_status(account_id)


@app.post("/api/accounts/{account_id}/refresh-session")
async def refresh_account_session(account_id: str) -> dict[str, Any]:
    try:
        account = database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    ensure_account_idle(account_id)
    try:
        result = await platform_registry.get(account["platform"]).refresh_session(
            database,
            account_id,
        )
        await preview_manager.invalidate(account_id)
        return result
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/accounts/{account_id}/preview")
async def get_account_preview(
    account_id: str,
    reload: bool = False,
) -> dict[str, Any]:
    try:
        return await preview_manager.screenshot(database, account_id, reload)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/accounts/{account_id}/open-browser")
async def open_account_browser(account_id: str) -> dict[str, Any]:
    ensure_account_idle(account_id)
    try:
        return await preview_manager.open_interactive(database, account_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/assignments/{assignment_id}/open")
async def open_assignment(assignment_id: str) -> dict[str, Any]:
    try:
        assignment = database.get_assignment(assignment_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    ensure_account_idle(assignment["account_id"])
    try:
        return await preview_manager.open_interactive(
            database,
            assignment["account_id"],
            target_url=assignment["task_url"],
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/accounts/{account_id}/collect")
async def collect_account(
    account_id: str,
    academic_year: int | None = None,
) -> dict[str, Any]:
    try:
        account = database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    ensure_account_idle(account_id)
    if await preview_manager.is_interactive(account_id):
        raise HTTPException(
            status_code=409,
            detail="该账号正在使用可操控浏览器，请关闭后再采集",
        )
    try:
        return await perform_collection(
            account_id,
            academic_year or current_academic_year(),
        )
    except Exception as error:
        logger.exception("Assignment collection failed")
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/accounts/{account_id}/collection-runs")
async def list_account_collection_runs(
    account_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    try:
        database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return database.list_collection_runs(account_id, limit)


@app.get("/api/collection-runs/{run_id}")
async def get_collection_run(run_id: str) -> dict[str, Any]:
    try:
        return database.get_collection_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


async def perform_collection(
    account_id: str,
    academic_year: int,
) -> dict[str, Any]:
    account = database.get_account(account_id)
    lock = collection_lock(account_id)
    if lock.locked():
        raise RuntimeError("该账号正在采集作业")
    run = database.create_collection_run(account_id, academic_year)
    database.update_account(
        account_id,
        last_collection_at=datetime.now(timezone.utc).isoformat(),
        last_collection_status="running",
        last_collection_error=None,
        last_collection_run_id=run["id"],
    )

    async def update_progress(payload: dict[str, Any]) -> None:
        fields = {
            key: payload[key]
            for key in (
                "progress",
                "current_course",
                "total_courses",
                "processed_courses",
                "failed_courses",
            )
            if key in payload
        }
        database.update_collection_run(run["id"], **fields)

    try:
        async with lock:
            report = await platform_registry.get(
                account["platform"]
            ).collect_assignments(
                database,
                account_id,
                academic_year,
                run_id=run["id"],
                progress=update_progress,
            )
    except Exception as error:
        database.finish_collection_run(
            run["id"],
            "failed",
            total_courses=0,
            processed_courses=0,
            failed_courses=0,
            assignment_count=0,
            submittable_count=0,
            error=str(error),
        )
        raise
    assignments = report["assignments"]
    if report["processed_courses"] == 0 and report["failed_courses"] > 0:
        error_message = "所有课程采集失败，请检查登录状态和网络"
        database.finish_collection_run(
            run["id"],
            "failed",
            total_courses=report["total_courses"],
            processed_courses=0,
            failed_courses=report["failed_courses"],
            assignment_count=0,
            submittable_count=0,
            error=error_message,
            errors=report["errors"],
        )
        raise RuntimeError(error_message)
    status = "partial" if report["failed_courses"] else "success"
    submittable_count = sum(
        1
        for item in assignments
        if item.get("status") == "unfinished" and item.get("submittable")
    )
    try:
        database.finalize_assignment_snapshot(
            account_id,
            academic_year,
            run["id"],
            set(report.get("failed_course_ids") or []),
        )
        finished = database.finish_collection_run(
            run["id"],
            status,
            total_courses=report["total_courses"],
            processed_courses=report["processed_courses"],
            failed_courses=report["failed_courses"],
            assignment_count=len(assignments),
            submittable_count=submittable_count,
            error=(
                f"{report['failed_courses']} 个课程采集失败"
                if report["failed_courses"]
                else None
            ),
            errors=report["errors"],
        )
    except Exception as error:
        database.finish_collection_run(
            run["id"],
            "failed",
            total_courses=report["total_courses"],
            processed_courses=report["processed_courses"],
            failed_courses=report["failed_courses"],
            assignment_count=len(assignments),
            submittable_count=submittable_count,
            error=f"保存采集结果失败：{error}",
            errors=report["errors"],
        )
        raise
    result = {
        "ok": True,
        "count": len(assignments),
        "submittable": submittable_count,
        "run_id": run["id"],
        "status": status,
        "total_courses": report["total_courses"],
        "processed_courses": report["processed_courses"],
        "failed_courses": report["failed_courses"],
        "errors": report["errors"],
        "finished": finished,
    }
    return result


@app.get("/api/platforms")
async def list_platforms() -> list[dict[str, str]]:
    return platform_registry.list()


@app.get("/api/assignments")
async def list_assignments(
    account_id: str | None = None,
    academic_year: int | None = None,
    submittable: bool = True,
) -> list[dict[str, Any]]:
    return database.list_assignments(
        account_id=account_id,
        academic_year=academic_year,
        submittable_only=submittable,
    )


@app.get("/api/semesters")
async def list_semesters(account_id: str | None = None) -> list[str]:
    assignments = database.list_assignments(
        account_id=account_id,
        submittable_only=False,
    )
    return sorted(
        {
            item["semester"]
            for item in assignments
            if item.get("semester")
        },
        reverse=True,
    )


@app.post("/api/jobs")
async def create_job(payload: CreateJobRequest) -> dict[str, Any]:
    try:
        assignment = database.get_assignment(payload.assignment_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    account = database.get_account(assignment["account_id"])
    if account["status"] != "ready":
        raise HTTPException(
            status_code=409,
            detail="账号尚未授权，请先刷新 Cookie",
        )
    if database.has_active_job_for_assignment(assignment["id"]):
        raise HTTPException(
            status_code=409,
            detail="该作业已有排队或运行中的任务",
        )
    try:
        parent_job_id, attempt = database.next_attempt_for_assignment(
            assignment["id"]
        )
        job = database.create_job(
            assignment["account_id"],
            assignment["id"],
            payload.auto_submit,
            payload.interactive,
            parent_job_id=parent_job_id,
            attempt=attempt,
        )
    except ActiveJobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    start_job(database, event_hub, effective_settings(), job["id"])
    return job


@app.get("/api/jobs")
async def list_jobs() -> list[dict[str, Any]]:
    return database.list_jobs()


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    try:
        return database.get_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/jobs/{job_id}/events")
async def get_job_events(job_id: str) -> list[dict[str, Any]]:
    return database.list_events(job_id)


@app.get("/api/jobs/{job_id}/artifacts")
async def get_job_artifacts(job_id: str) -> list[dict[str, Any]]:
    return database.list_artifacts(job_id)


@app.post("/api/jobs/{job_id}/pause")
async def pause_agent_job(job_id: str) -> dict[str, Any]:
    try:
        pause_job(database, event_hub, job_id)
        return database.get_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/resume")
async def resume_agent_job(job_id: str) -> dict[str, Any]:
    try:
        resume_job(database, event_hub, job_id)
        return database.get_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/stop")
async def stop_agent_job(job_id: str) -> dict[str, Any]:
    try:
        stop_job(database, event_hub, job_id)
        return database.get_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/focus-browser")
async def focus_agent_browser(job_id: str) -> dict[str, Any]:
    try:
        await focus_job_browser(job_id)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"ok": True}


@app.get("/api/jobs/{job_id}/snapshot")
async def get_job_snapshot(job_id: str) -> dict[str, Any]:
    try:
        return await capture_job_snapshot(job_id)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/finish-intervention")
async def finish_intervention(job_id: str) -> dict[str, Any]:
    try:
        return await finish_job_intervention(database, event_hub, job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/retry")
async def retry_agent_job(job_id: str) -> dict[str, Any]:
    try:
        original = database.get_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if original["status"] in {"queued", "running", "paused", "stopping"}:
        raise HTTPException(status_code=409, detail="任务仍在运行，不能重试")
    account = database.get_account(original["account_id"])
    if account["status"] != "ready":
        raise HTTPException(
            status_code=409,
            detail="账号尚未授权，请先刷新 Cookie",
        )
    if database.has_active_job_for_assignment(original["assignment_id"]):
        raise HTTPException(
            status_code=409,
            detail="该作业已有排队或运行中的任务",
        )
    try:
        parent_job_id, attempt = database.next_attempt_for_assignment(
            original["assignment_id"]
        )
        job = database.create_job(
            original["account_id"],
            original["assignment_id"],
            bool(original["auto_submit"]),
            bool(original["interactive"]),
            parent_job_id=parent_job_id,
            attempt=attempt,
        )
    except ActiveJobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    start_job(database, event_hub, effective_settings(), job["id"])
    return job


@app.post("/api/jobs/{job_id}/submission-status")
async def verify_job_submission(
    job_id: str,
    payload: SubmissionVerificationRequest,
) -> dict[str, Any]:
    try:
        job = database.update_submission_status(
            job_id,
            payload.status,
            payload.note.strip(),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    event = database.add_event(
        job_id,
        "submission_verified"
        if payload.status in {"submitted_verified", "filled_verified"}
        else "submission_reviewed",
        (
            "已人工确认提交成功"
            if payload.status == "submitted_verified"
            else (
                "已人工确认填写结果"
                if payload.status == "filled_verified"
                else "已更新人工核对结果"
            )
        ),
        {"submission_status": payload.status, "note": payload.note.strip()},
    )
    await event_hub.publish(job_id, event)
    return job


@app.get("/api/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str) -> FileResponse:
    artifacts = [
        artifact
        for job in database.list_jobs()
        for artifact in database.list_artifacts(job["id"])
    ]
    artifact = next(
        (item for item in artifacts if item["id"] == artifact_id),
        None,
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="凭证不存在")
    path = Path(artifact["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


@app.websocket("/ws/jobs/{job_id}")
async def job_websocket(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    queue = event_hub.subscribe(job_id)
    try:
        for event in database.list_events(job_id):
            await websocket.send_json(event)
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        event_hub.unsubscribe(job_id, queue)


@app.post("/api/notifications/test")
async def test_notification(payload: TestWebhookRequest) -> dict[str, Any]:
    effective = effective_settings()
    sent = await send_completion_notification(
        effective,
        {"event": "test", "message": payload.message},
    )
    configured = bool(
        settings.webhook_url
        or (
            effective.weixin_bot_token
            and effective.weixin_user_id
            and effective.weixin_context_token
        )
    )
    return {"ok": any(sent.values()), "configured": configured, "channels": sent}
