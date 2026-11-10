from __future__ import annotations

import asyncio
import dataclasses
import shutil
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import httpx

from .config import settings
from .db import Database
from .events import event_hub
from .services.platforms import platform_registry
from .services.agent_runner import (
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    database.mark_interrupted_jobs()
    logger.info("Local AI assignment studio started")
    yield
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
    base_url: str
    api_key: str = ""
    models: list[str] = Field(default_factory=list)


class WeixinStatusRequest(BaseModel):
    code: str
    verify_code: str = ""


def get_model_config() -> dict[str, Any]:
    stored = database.get_setting("model_config", {}) or {}
    return {
        "provider_name": stored.get("provider_name", "ssh"),
        "base_url": stored.get("base_url", settings.openai_base_url),
        "api_key": stored.get("api_key", settings.openai_api_key),
        "models": stored.get("models", [settings.openai_model]),
    }


def effective_settings() -> Any:
    config = get_model_config()
    weixin = database.get_setting("weixin_binding", {}) or {}
    return dataclasses.replace(
        settings,
        openai_base_url=config["base_url"],
        openai_api_key=config["api_key"],
        openai_model=(config.get("models") or [settings.openai_model])[0],
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
        "version": "0.1.0",
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
    }


@app.put("/api/model-config")
async def save_model_settings(payload: ModelConfigRequest) -> dict[str, Any]:
    existing = get_model_config()
    api_key = payload.api_key.strip() or existing["api_key"]
    models = [item.strip() for item in payload.models if item.strip()]
    if not models:
        raise HTTPException(status_code=400, detail="至少添加一个模型")
    database.set_setting(
        "model_config",
        {
            "provider_name": payload.provider_name.strip() or "custom",
            "base_url": payload.base_url.rstrip("/"),
            "api_key": api_key,
            "models": models,
        },
    )
    return {"ok": True, "models": models}


@app.post("/api/model-config/fetch-models")
async def fetch_provider_models(payload: ModelConfigRequest) -> dict[str, Any]:
    config = get_model_config()
    api_key = payload.api_key.strip() or config["api_key"]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{payload.base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        data = response.json()
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
async def collect_account(account_id: str, academic_year: int = 2026) -> dict[str, Any]:
    try:
        account = database.get_account(account_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    ensure_account_idle(account_id)
    try:
        assignments = await platform_registry.get(
            account["platform"]
        ).collect_assignments(database, account_id, academic_year)
    except Exception as error:
        logger.exception("Assignment collection failed")
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "ok": True,
        "count": len(assignments),
        "submittable": sum(
            1
            for item in assignments
            if item.get("status") == "unfinished" and item.get("submittable")
        ),
    }


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
    job = database.create_job(
        assignment["account_id"],
        assignment["id"],
        payload.auto_submit,
        payload.interactive,
    )
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
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/resume")
async def resume_agent_job(job_id: str) -> dict[str, Any]:
    try:
        resume_job(database, event_hub, job_id)
        return database.get_job(job_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/stop")
async def stop_agent_job(job_id: str) -> dict[str, Any]:
    try:
        stop_job(database, event_hub, job_id)
        return database.get_job(job_id)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/jobs/{job_id}/retry")
async def retry_agent_job(job_id: str) -> dict[str, Any]:
    try:
        original = database.get_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if original["status"] in {"queued", "running", "paused", "stopping"}:
        raise HTTPException(status_code=409, detail="任务仍在运行，不能重试")
    job = database.create_job(
        original["account_id"],
        original["assignment_id"],
        bool(original["auto_submit"]),
        bool(original["interactive"]),
    )
    start_job(database, event_hub, effective_settings(), job["id"])
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
