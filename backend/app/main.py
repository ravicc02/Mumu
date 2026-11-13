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
from .preferences import PreferencesUpdate, AccountSchedule, load_preferences, collection_interval_seconds
from .services.assignment_automation import save_preferences, record_discovery, pending_assignments, mark_pending, automation_status
from .db import ActiveJobConflict, Database
from .version import __version__
from .events import event_hub
from .services.platforms import platform_registry
from .services.platforms.catalog import platform_spec, platform_url, require_business
from .services import platform_auth
from .services.agent_runner import (
    capture_job_snapshot,
    close_preserved_sessions,
    continue_job_intervention,
    finish_job_intervention,
    focus_job_browser,
    job_browser_state,
    job_live_browser,
    pause_job,
    prepare_job_retry,
    resume_job,
    review_job_submission,
    start_job,
    stop_job,
    shutdown_running_jobs,
)
from .services.job_state import annotate_queues
from .services.notifier import notification_preferences, send_completion_notification, notification_examples
from .services.collection_notifications import initialize_baseline, ledger_key, notify_new_assignments
from .services import weixin_connector
from .services import chaoxing
from .services.assignment_readiness import execution_block
from .services.preview import preview_manager
from .services.learning.service import LearningService
from .services.learning.routes import learning_router
from .services.attendance.service import AttendanceService
from .services.attendance.routes import attendance_router, ShareGateway
from .services.browser_stream import BrowserTickets, serve_browser
from .diagnostics.logging import configure_logging
from .diagnostics.routes import diagnostic_router, install_request_diagnostics


settings.ensure_directories()
configure_logging(settings)
logger = logging.getLogger("answer-studio")
database = Database(settings)
learning_service = LearningService(lambda: database, settings, event_hub, settings_provider=lambda: effective_settings())
attendance_service = AttendanceService(lambda: database)
attendance_gateway = ShareGateway(attendance_service, settings.port + 1)
job_browser_tickets = BrowserTickets()


def job_payload(job: dict[str, Any]) -> dict[str, Any]:
    all_jobs = annotate_queues([job_browser_state(item) for item in database.list_jobs()])
    return next((item for item in all_jobs if item["id"] == job["id"]), job_browser_state(job))


_collection_locks: dict[str, asyncio.Lock] = {}
_scheduled_collection_tasks: dict[str, asyncio.Task] = {}


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
    return datetime.now(timezone.utc) - last >= timedelta(seconds=collection_interval_seconds(account))


def current_academic_year() -> int:
    today = datetime.now()
    return today.year if today.month >= 8 else today.year - 1


async def collect_scheduled_account(account_id: str) -> None:
    try:
        await perform_collection(account_id, current_academic_year(), scheduled=True)
        logger.info("Scheduled collection completed for account %s", account_id)
    except Exception:
        logger.exception("Scheduled collection failed for account %s", account_id)


async def schedule_due_collections() -> None:
    limit = load_preferences(database).system.collection_concurrency
    for account in database.list_accounts():
        if len(_scheduled_collection_tasks) >= limit:
            break
        if (not platform_spec(account["platform"])["assignments"]
                or account["id"] in _scheduled_collection_tasks or account["status"] != "ready"
                or not collection_due(account) or database.has_active_job(account["id"])
                or learning_service.active(account["id"])
                or collection_lock(account["id"]).locked()
                or platform_login_in_progress(account["id"])
                or await preview_manager.is_interactive(account["id"])):
            continue
        task = asyncio.create_task(collect_scheduled_account(account["id"]))
        _scheduled_collection_tasks[account["id"]] = task
        task.add_done_callback(lambda finished, identifier=account["id"]: _scheduled_collection_tasks.pop(identifier, None))


async def run_collection_scheduler() -> None:
    try:
        await asyncio.sleep(15)
        while True:
            try:
                await dispatch_automatic_assignments()
                await schedule_due_collections()
            except Exception:
                logger.exception("Automatic scheduler tick failed")
            await asyncio.sleep(1)
    finally:
        pending = list(_scheduled_collection_tasks.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        _scheduled_collection_tasks.clear()


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
    learning_service.initialize()
    attendance_service.initialize()
    if attendance_service.store.has_live_shares():
        try: await attendance_gateway.ensure_started()
        except ValueError: logger.warning('Attendance share gateway could not resume',exc_info=True)
    database.diagnostics.prune()
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
    from .services.account_identity import sync_legacy_chaoxing_identities
    identity_sync_task = asyncio.create_task(sync_legacy_chaoxing_identities(database))
    logger.info("Local AI assignment studio started")
    try:
        yield
    finally:
        scheduler_task.cancel()
        health_monitor_task.cancel()
        identity_sync_task.cancel()
        await asyncio.gather(
            scheduler_task,
            health_monitor_task,
            identity_sync_task,
            return_exceptions=True,
        )
        await shutdown_running_jobs(database, event_hub)
        await learning_service.shutdown()
        await attendance_gateway.close()
        await attendance_service.shutdown()
        await platform_auth.shutdown()
        await close_preserved_sessions()
        await preview_manager.close()
        logger.info("Local AI assignment studio stopped")


app = FastAPI(title="Local AI Assignment Studio", version=__version__, lifespan=lifespan)
app.include_router(diagnostic_router(lambda: database))
app.include_router(attendance_router(attendance_service, attendance_gateway, lambda activity: preview_manager.open_interactive(
    database, activity['account_id'], task_id='attendance-'+activity['id'],
    target_url='https://mobilelearn.chaoxing.com/widget/sign/pcStuSignController/preSign?activeId='+activity['upstream_id']+'&courseId='+activity['course_id']+'&classId='+activity['class_id'])))
from .services.educoder.routes import router as educoder_router
app.include_router(educoder_router(lambda: database, lambda identifier: ensure_account_idle(identifier), collection_lock))
install_request_diagnostics(app, lambda: database, lambda: effective_settings())
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://localhost:5174",
        "http://127.0.0.1:8877",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateAccountRequest(BaseModel):
    name: str = Field(default="", max_length=100)
    platform: str = "chaoxing"
    entry_url: str = Field(default="", max_length=2048)


class UpdateAccountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CreateJobRequest(BaseModel):
    assignment_id: str
    auto_submit: bool = False
    auto_evaluate: bool | None = None
    interactive: bool = True


class RetryJobRequest(BaseModel):
    auto_submit: bool | None = None
    auto_evaluate: bool | None = None


class ContinueJobRequest(BaseModel):
    auto_evaluate: bool | None = None


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


class NotificationPreferencesRequest(BaseModel):
    new_assignments_enabled: bool | None = None
    completion_enabled: bool | None = None


class CollectionScheduleRequest(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    interval_value: float | None = None
    interval_unit: str | None = None
    auto_execute: bool | None = None


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
        agent_max_steps=load_preferences(database).assignments.max_steps,
        weixin_bot_token=weixin.get("token", ""),
        weixin_user_id=weixin.get("user_id", ""),
        weixin_context_token=weixin.get("context_token", ""),
        weixin_base_url=weixin.get("base", settings.weixin_base_url),
    )


def platform_login_in_progress(account_id):
    return chaoxing.login_in_progress(account_id) or platform_auth.login_in_progress(account_id)


def ensure_profile_available(account_id: str) -> None:
    if platform_login_in_progress(account_id):
        raise HTTPException(409, "该账号正在授权，请先完成登录")
    if collection_lock(account_id).locked():
        raise HTTPException(409, "该账号正在采集，请等待采集结束")


def ensure_account_idle(account_id: str) -> None:
    if attendance_service.active(account_id):
        raise HTTPException(409, '该账号正在采集或执行签到，请先在签到页面停止操作')
    learning = learning_service.active(account_id)
    if learning:
        raise HTTPException(status_code=409, detail=f"该账号正在处理网课「{learning['course_name']}」，请先在网课页面停止任务")
    if platform_login_in_progress(account_id):
        raise HTTPException(status_code=409, detail="该账号正在授权，请先完成或关闭登录浏览器")
    if collection_lock(account_id).locked():
        raise HTTPException(status_code=409, detail="该账号正在采集，请等待采集结束后操作浏览器")
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
        "version": __version__,
        "data_root": str(settings.data_root),
        "model": (model_config["models"] or [settings.openai_model])[0],
        "ai_configured": bool(model_config["api_key"]),
    }


@app.get("/api/accounts")
async def list_accounts() -> list[dict[str, Any]]:
    return [{**account, "capabilities": platform_spec(account["platform"]), "learning_run": learning_service.active(account["id"]), "automation": automation_status(database, account["id"])} for account in database.list_accounts()]


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    config = get_model_config()
    effective = effective_settings()
    return {
        "data_root": str(settings.data_root),
        "logs_root": str(settings.logs_root),
        "main_log_path": str(settings.logs_root / "main.log"),
        "old_main_log_path": str(settings.logs_root / "oldmain.log"),
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
        "weixin_ready": bool(effective.weixin_bot_token and effective.weixin_user_id and effective.weixin_context_token)
        and not (database.get_setting("weixin_delivery", {}) or {}).get("error"),
    }


@app.get("/api/preferences")
async def get_preferences() -> dict[str, Any]:
    return load_preferences(database).model_dump()


@app.patch("/api/preferences")
async def update_preferences(payload: PreferencesUpdate) -> dict[str, Any]:
    try:
        result = save_preferences(database, payload)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    for job in database.list_jobs():
        if job.get("trigger") == "scheduled" and job["status"] == "queued":
            account = database.get_account(job["account_id"])
            if not account["auto_execute_enabled"] or not account["auto_collect_enabled"]:
                stop_job(database, event_hub, job["id"])
    return result


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


@app.get("/api/connectors/weixin")
async def get_weixin_connector() -> dict[str, Any]:
    binding = database.get_setting("weixin_binding", {}) or {}
    ledgers = [database.get_setting(ledger_key(account["id"], current_academic_year()), {}) or {}
               for account in database.list_accounts()]
    pending = [ledger for ledger in ledgers if ledger.get("pending")]
    delivery = database.get_setting("weixin_delivery",{}) or {}
    return {
        "configured": bool(binding.get("token") and binding.get("user_id")),
        "ready": bool(binding.get("token") and binding.get("user_id") and binding.get("context_token")) and not delivery.get("error"),
        "preferences": notification_preferences(database),
        "examples": notification_examples(),
        "pending_new_assignments": sum(len(ledger["pending"]) for ledger in pending),
        "last_delivery_error": delivery.get("error") or next((ledger["last_error"] for ledger in pending if ledger.get("last_error")), None),
    }


@app.patch("/api/connectors/weixin/preferences")
async def update_notification_preferences(payload: NotificationPreferencesRequest) -> dict[str, bool]:
    preferences = {**notification_preferences(database), **payload.model_dump(exclude_none=True)}
    database.set_setting("notification_preferences", preferences)
    return preferences


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
        if payload.entry_url: platform_url(payload.platform, payload.entry_url)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    account = database.create_account(payload.name.strip() or (platform_spec(payload.platform)["label"] + " · 待授权"), payload.platform, settings)
    if payload.entry_url: database.update_account(account["id"], entry_url=payload.entry_url)
    return database.get_account(account["id"])


@app.patch("/api/accounts/{account_id}")
async def update_account(
    account_id: str,
    payload: UpdateAccountRequest,
) -> dict[str, Any]:
    try:
        account = database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if account["platform"] == "chaoxing":
        raise HTTPException(409, "学习通账号名称由平台同步，请通过重新授权更新资料。")
    database.update_account(account_id, name=payload.name.strip())
    return database.get_account(account_id)


@app.get("/api/accounts/{account_id}/avatar")
async def get_account_avatar(account_id: str):
    try: account = database.get_account(account_id)
    except KeyError as error: raise HTTPException(404,"账号不存在") from error
    avatar = (Path(account["profile_dir"])/"avatar.bin").resolve()
    if not avatar.is_relative_to(database.settings.profiles_root.resolve()) or not avatar.is_file() or account.get("avatar_mime") not in {"image/png","image/jpeg","image/webp"}:
        raise HTTPException(404,"平台头像尚未同步")
    return FileResponse(avatar,media_type=account["avatar_mime"],headers={"Cache-Control":"private, max-age=3600","X-Content-Type-Options":"nosniff"})


@app.patch("/api/accounts/{account_id}/collection-schedule")
async def update_collection_schedule(
    account_id: str,
    payload: CollectionScheduleRequest,
) -> dict[str, Any]:
    try:
        account = database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    value = payload.interval_value if payload.interval_value is not None else account["auto_collect_interval_value"]
    unit = payload.interval_unit or account["auto_collect_interval_unit"]
    if payload.interval_minutes is not None:
        if payload.interval_value is not None or payload.interval_unit is not None:
            raise HTTPException(400, "请使用一种检测间隔格式")
        value, unit = payload.interval_minutes, "min"
    enabled = payload.enabled if payload.enabled is not None else bool(account["auto_collect_enabled"])
    auto_execute = payload.auto_execute if payload.auto_execute is not None else bool(account["auto_execute_enabled"])
    if not enabled: auto_execute = False
    try:
        schedule = AccountSchedule(account_id=account_id, enabled=enabled, auto_execute=auto_execute,
                                   interval_value=value, interval_unit=unit)
        await update_preferences(PreferencesUpdate(schedules=[schedule]))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
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


@app.post("/api/accounts/{account_id}/confirm-authorization")
async def confirm_platform_authorization(account_id: str):
    try: return await platform_auth.confirm_login(database, account_id)
    except (ValueError, RuntimeError) as error: raise HTTPException(409, str(error)) from error
    except KeyError as error: raise HTTPException(404, str(error)) from error


@app.post("/api/accounts/{account_id}/login")
async def login_account(account_id: str) -> dict[str, Any]:
    try:
        account = database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    ensure_account_idle(account_id)
    await preview_manager.invalidate(account_id)
    ensure_account_idle(account_id)
    return platform_registry.get(account["platform"]).start_login(
        database, account_id, settings
    )


@app.get("/api/accounts/{account_id}/login-status")
async def get_login_status(account_id: str) -> dict[str, Any]:
    result = chaoxing.login_status(account_id)
    if result.get("status") == "success" and result.get("account_id"):
        try:
            database.get_account(result["account_id"])
        except KeyError as error:
            raise HTTPException(404, "授权记录已移除") from error
        return result
    try:
        account = database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(404, "账号不存在") from error
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
    ensure_profile_available(account_id)
    try:
        return await preview_manager.screenshot(database, account_id, reload)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/accounts/{account_id}/open-browser")
async def open_account_browser(account_id: str) -> dict[str, Any]:
    ensure_profile_available(account_id)
    try:
        return await preview_manager.open_interactive(database, account_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/assignments/{assignment_id}/inspect")
async def inspect_assignment(assignment_id: str) -> dict[str, Any]:
    try:
        assignment = database.get_assignment(assignment_id)
        account = database.get_account(assignment["account_id"])
        if account["status"] != "ready": raise HTTPException(409, "请先完成账号授权")
        if account["platform"] == "educoder":
            from .services.educoder.adapter import verify_assignment
            ensure_profile_available(account["id"])
            await verify_assignment(database,assignment_id)
            return database.get_assignment(assignment_id)
        if account["platform"] != "chaoxing": raise HTTPException(409, "该平台尚未支持作答要求检查")
        ensure_profile_available(account["id"])
        return await chaoxing.inspect_assignment(database, assignment_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, "未能读取作业页面，请检查授权后重试") from error


@app.post("/api/assignments/{assignment_id}/open")
async def open_assignment(assignment_id: str) -> dict[str, Any]:
    try:
        assignment = database.get_assignment(assignment_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    ensure_profile_available(assignment["account_id"])
    active = next((job_browser_state(item) for item in database.list_jobs() if item["assignment_id"] == assignment_id and job_browser_state(item)["browser_available"]), None)
    if active:
        await focus_job_browser(active["id"])
        return {"ok": True, "message": "已打开该作业的独立任务浏览器", "job_id": active["id"]}
    try:
        return await preview_manager.open_interactive(
            database,
            assignment["account_id"],
            target_url=assignment["task_url"],
            task_id=assignment_id,
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def assignment_account(account_id):
    try: account = database.get_account(account_id)
    except KeyError as error: raise HTTPException(404, str(error)) from error
    try: require_business(account["platform"], "assignments")
    except ValueError as error: raise HTTPException(409, str(error)) from error
    return account


@app.post("/api/accounts/{account_id}/collect")
async def collect_account(
    account_id: str,
    academic_year: int | None = None,
) -> dict[str, Any]:
    account = assignment_account(account_id)
    ensure_account_idle(account_id)
    if await preview_manager.is_interactive(account_id):
        raise HTTPException(
            status_code=409,
            detail="该账号正在使用可操控浏览器，请关闭后再采集",
        )
    try:
        return await perform_collection(
            account_id,
            0 if account["platform"] == "educoder" else academic_year or current_academic_year(),
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
    *,
    scheduled: bool = False,
) -> dict[str, Any]:
    ensure_account_idle(account_id)
    async with collection_lock(account_id):
        return await _perform_collection_locked(account_id, academic_year, scheduled=scheduled)


async def _perform_collection_locked(account_id: str, academic_year: int, *, scheduled: bool) -> dict[str, Any]:
    account = database.get_account(account_id)
    if account["platform"] == "educoder": academic_year = 0
    if not platform_spec(account["platform"])["assignments"]:
        raise HTTPException(409,"当前平台请在网课页面采集课程")
    initialize_baseline(database, account_id, academic_year)
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
        report = await platform_registry.get(account["platform"]).collect_assignments(
            database, account_id, academic_year, run_id=run["id"], progress=update_progress,
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
    if report["failed_courses"] > 0 and (report["processed_courses"] == 0 or report["failed_courses"] == report["total_courses"]):
        error_message = "全部课程采集失败，请展开失败详情查看原因"
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
            course_ids=report.get("scope_course_ids"),
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
    verified_ids = {item.get("id") for item in assignments}
    verified = [item for item in database.list_assignments(account_id, academic_year)
                if item["id"] in verified_ids]
    record_discovery(database, account_id, verified, scheduled=scheduled, complete=status == "success")
    try:
        notification = await notify_new_assignments(database, effective_settings(), account, academic_year,
            verified, scheduled=scheduled, sender=send_completion_notification)
    except Exception as error:
        logger.exception("Collection succeeded but notification bookkeeping failed for account %s", account_id)
        notification = {"status": "failed", "error": str(error)}
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
        "notification": notification,
    }
    return result


@app.get("/api/platforms")
async def list_platforms() -> list[dict[str, Any]]:
    return platform_registry.list()


@app.get("/api/assignments")
async def list_assignments(
    account_id: str | None = None,
    academic_year: int | None = None,
    submittable: bool = True,
) -> list[dict[str, Any]]:
    if account_id: assignment_account(account_id)
    records = database.list_assignments(
        account_id=account_id,
        academic_year=academic_year,
        submittable_only=submittable,
    )
    if account_id and database.get_account(account_id)["platform"] == "educoder":
        from .services.educoder.store import courses
        selected = {item["id"] for item in courses(database,account_id) if item["selected"] and item["active"]}
        records = [item for item in records if item["course_id"] in selected]
    return records


@app.get("/api/semesters")
async def list_semesters(account_id: str | None = None) -> list[str]:
    if account_id: assignment_account(account_id)
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


def ensure_assignment_execution_allowed(assignment_id: str) -> None:
    assignment_account(database.get_assignment(assignment_id)["account_id"])
    from .services.assignment_availability import assignment_expired
    if assignment_expired(database.get_assignment(assignment_id)):
        raise HTTPException(409, "作业已超过截止时间，请重新采集确认最新开放时间。")
    attempts = [job for job in database.list_jobs() if job["assignment_id"] == assignment_id]
    if any(job["status"] == "completed" for job in attempts):
        raise HTTPException(status_code=409, detail="该作业已确认完成并提交，无需重复执行")
    if attempts and attempts[0]["submission_status"] in {"submission_unknown", "submitted_unverified"}:
        raise HTTPException(status_code=409, detail="该作业提交状态尚未确认，请先在任务详情核对是否已提交，再重试")


async def enqueue_assignment(payload: CreateJobRequest, *, trigger="manual") -> dict[str, Any]:
    try:
        assignment = database.get_assignment(payload.assignment_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    account = database.get_account(assignment["account_id"])
    if account["platform"] == "educoder":
        from .services.educoder.store import courses
        if not any(item["selected"] and item["active"] and item["accessible"] and item["id"] == assignment["course_id"] for item in courses(database,account["id"])):
            raise HTTPException(409,"该实验不在当前选择的课堂范围内。")
    if execution_block(assignment):
        raise HTTPException(409, execution_block(assignment))
    if not assignment["submittable"]:
        raise HTTPException(status_code=409, detail="该作业当前不能提交，请重新采集后确认状态")
    if not effective_settings().openai_api_key:
        raise HTTPException(status_code=409, detail="请先在设置中配置并测试模型连接")
    if account["status"] != "ready":
        raise HTTPException(
            status_code=409,
            detail="账号尚未授权，请先刷新 Cookie",
        )
    if platform_login_in_progress(account["id"]):
        raise HTTPException(status_code=409, detail="当前账号仍在授权，请等待登录完成后执行")
    if collection_lock(account["id"]).locked():
        raise HTTPException(status_code=409, detail="当前账号正在采集，请等待采集结束后执行")
    if database.has_active_job_for_assignment(assignment["id"]):
        raise HTTPException(
            status_code=409,
            detail="该作业已有排队或运行中的任务",
        )
    ensure_assignment_execution_allowed(assignment["id"])
    try:
        parent_job_id, attempt = database.next_attempt_for_assignment(
            assignment["id"]
        )
        job = database.create_job(
            assignment["account_id"],
            assignment["id"],
            (load_preferences(database).assignments.educoder_auto_evaluate if payload.auto_evaluate is None else payload.auto_evaluate)
            if account['platform'] == 'educoder' else payload.auto_submit,
            payload.interactive,
            parent_job_id=parent_job_id,
            attempt=attempt,
            trigger=trigger,
        )
    except ActiveJobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    start_job(database, event_hub, effective_settings(), job["id"])
    return job_payload(job)


@app.post("/api/jobs")
async def create_job(payload: CreateJobRequest) -> dict[str, Any]:
    return await enqueue_assignment(payload)


async def dispatch_automatic_assignments() -> None:
    preferences = load_preferences(database).assignments
    jobs = [job_browser_state(job) for job in database.list_jobs()]
    running = [job for job in jobs if job.get("trigger") == "scheduled" and
               job["status"] in {"queued", "running", "paused", "stopping"}]
    slots = max(0, preferences.concurrency - len(running))
    for item in pending_assignments(database):
        account = database.get_account(item["account_id"])
        identifier = item["assignment_id"]
        if not account["auto_execute_enabled"] or not account["auto_collect_enabled"]:
            mark_pending(database, identifier, reason="自动完成已关闭", state="cancelled")
            continue
        if not item["active"] or not item["submittable"] or item["assignment_status"] not in {"unfinished", "未交", "未完成"}:
            mark_pending(database, identifier, reason="作业已完成或不再可提交", state="skipped")
            continue
        if any(job["assignment_id"] == identifier for job in jobs):
            mark_pending(database, identifier, reason="作业已有执行记录", state="skipped")
            continue
        if execution_block(database.get_assignment(identifier)):
            mark_pending(database, identifier, reason=execution_block(database.get_assignment(identifier)), state="skipped")
            continue
        reason = ""
        if account["status"] != "ready": reason = "等待账号重新授权"
        elif not effective_settings().openai_api_key: reason = "等待配置模型"
        elif collection_lock(account["id"]).locked(): reason = "等待采集结束"
        elif platform_login_in_progress(account["id"]): reason = "等待账号授权结束"
        elif slots <= 0: reason = "等待自动执行名额"
        if reason:
            mark_pending(database, identifier, reason=reason)
            continue
        try:
            job = await enqueue_assignment(CreateJobRequest(assignment_id=identifier,
                auto_submit=preferences.auto_submit, interactive=preferences.interactive), trigger="scheduled")
        except HTTPException as error:
            mark_pending(database, identifier, reason=str(error.detail), state="skipped")
        except Exception:
            logger.exception("Automatic assignment dispatch failed for %s", identifier)
            mark_pending(database, identifier, reason="任务创建失败，请检查后台日志", state="failed")
        else:
            jobs.append(job)
            slots -= 1


@app.get("/api/jobs")
async def list_jobs() -> list[dict[str, Any]]:
    all_jobs = annotate_queues([job_browser_state(job) for job in database.list_jobs()])
    return [job for job in all_jobs if not job.get("deleted_at")]


@app.delete("/api/jobs/{job_id}")
async def delete_job_task(job_id: str) -> dict[str, Any]:
    try:
        job = job_payload(database.get_job(job_id))
        if "delete" not in job["actions"] and not job.get("deleted_at"):
            raise ActiveJobConflict("请先停止任务并关闭任务浏览器，再删除任务。")
        return database.delete_job_task(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ActiveJobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    try:
        return job_payload(database.get_job(job_id))
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
        return job_payload(database.get_job(job_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/resume")
async def resume_agent_job(job_id: str) -> dict[str, Any]:
    try:
        resume_job(database, event_hub, job_id)
        return job_payload(database.get_job(job_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/stop")
async def stop_agent_job(job_id: str) -> dict[str, Any]:
    try:
        stop_job(database, event_hub, job_id)
        return job_payload(database.get_job(job_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
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


@app.post("/api/jobs/{job_id}/browser/session")
async def job_browser_session(job_id: str):
    try:
        job_live_browser(job_id)
        return job_browser_tickets.issue(job_id)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.websocket("/api/jobs/{job_id}/browser")
async def job_browser_stream(websocket: WebSocket, job_id: str):
    trace = database.diagnostics.trace("assignment", job_id)
    await serve_browser(websocket, job_id, job_browser_tickets.consume, job_live_browser,
                        on_error=lambda error: trace.exception(error, phase="live_browser"))


@app.post("/api/jobs/{job_id}/finish-intervention")
async def finish_intervention(job_id: str) -> dict[str, Any]:
    try:
        return job_payload(await finish_job_intervention(database, event_hub, job_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/retry")
async def retry_agent_job(job_id: str, payload: RetryJobRequest | None = None) -> dict[str, Any]:
    try:
        original = database.get_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if original["status"] in {"queued", "running", "paused", "stopping"}:
        raise HTTPException(status_code=409, detail="任务仍在运行，不能重试")
    blocked = execution_block(database.get_assignment(original["assignment_id"]))
    if blocked: raise HTTPException(409, blocked)
    account = database.get_account(original["account_id"])
    if account["status"] != "ready":
        raise HTTPException(
            status_code=409,
            detail="账号尚未授权，请先刷新 Cookie",
        )
    if platform_login_in_progress(account["id"]):
        raise HTTPException(status_code=409, detail="当前账号仍在授权，请等待登录完成后重试")
    if collection_lock(account["id"]).locked():
        raise HTTPException(status_code=409, detail="当前账号正在采集，请等待采集结束后重试")
    if not original.get("assignment_submittable"):
        raise HTTPException(status_code=409, detail="该作业当前不能提交，请重新采集后核对")
    if not effective_settings().openai_api_key:
        raise HTTPException(status_code=409, detail="请先配置并测试模型连接，再重试任务")
    ensure_assignment_execution_allowed(original["assignment_id"])
    try:
        original = await prepare_job_retry(database, job_id)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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
            (payload.auto_evaluate if payload and payload.auto_evaluate is not None else load_preferences(database).assignments.educoder_auto_evaluate)
            if account['platform'] == 'educoder' else (payload.auto_submit if payload and payload.auto_submit is not None else bool(original["auto_submit"])),
            bool(original["interactive"]),
            parent_job_id=parent_job_id,
            attempt=attempt,
        )
    except ActiveJobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    start_job(database, event_hub, effective_settings(), job["id"])
    return job_payload(job)


@app.post("/api/jobs/{job_id}/continue")
async def continue_agent_job(job_id: str, payload: ContinueJobRequest | None = None) -> dict[str, Any]:
    try:
        job = database.get_job(job_id)
        blocked = execution_block(database.get_assignment(job["assignment_id"]))
        if blocked: raise HTTPException(409, blocked)
        return job_payload(await continue_job_intervention(database, event_hub, effective_settings(), job_id,
            auto_evaluate=payload.auto_evaluate if payload else None))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/submission-status")
async def verify_job_submission(
    job_id: str,
    payload: SubmissionVerificationRequest,
) -> dict[str, Any]:
    try:
        job = await review_job_submission(
            database,
            event_hub,
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
    return job_payload(job)


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
async def job_websocket(websocket: WebSocket, job_id: str, after_id: int = 0) -> None:
    await websocket.accept()
    queue = event_hub.subscribe(job_id)
    last_sent = max(0, after_id)
    async def wait_for_disconnect() -> None:
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass

    disconnected = asyncio.create_task(wait_for_disconnect())
    pending_event: asyncio.Task | None = None
    try:
        for event in database.list_events(job_id, after_id=last_sent):
            await websocket.send_json(event)
            last_sent = max(last_sent, event["id"])
        while True:
            pending_event = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({pending_event, disconnected}, return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done:
                await disconnected
                break
            event = pending_event.result()
            if event.get("id") is not None and event["id"] <= last_sent:
                continue
            await websocket.send_json(event)
            if event.get("id") is not None:
                last_sent = event["id"]
    except WebSocketDisconnect:
        pass
    finally:
        tasks = [task for task in (pending_event, disconnected) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        event_hub.unsubscribe(job_id, queue)


def ensure_learning_start(account_id: str) -> None:
    account = database.get_account(account_id)
    if account["status"] != "ready": raise RuntimeError("账号尚未授权，请先刷新 Cookie")
    if not platform_spec(account["platform"])["learning"]: raise RuntimeError("当前平台没有接入网课")
    if platform_login_in_progress(account_id): raise RuntimeError("该账号正在授权，请先完成登录")
    if collection_lock(account_id).locked(): raise RuntimeError("该账号正在采集，请等待结束")


app.include_router(learning_router(learning_service, ensure_account_idle, ensure_learning_start, collection_lock))


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
