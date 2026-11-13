from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .bundle import build_bundle
from .logging import current_trace, current_log_settings
from .privacy import task_scrubber


class RendererContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_agent: str = Field(default="", max_length=500)
    timezone: str = Field(default="", max_length=80)
    language: str = Field(default="", max_length=30)
    width: int = Field(default=0, ge=0, le=32768)
    height: int = Field(default=0, ge=0, le=32768)
    dpr: float = Field(default=1, ge=.1, le=10)


class DiagnosticExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_content: bool = False
    include_screenshots: bool = False
    renderer: RendererContext | None = None


def diagnostic_router(database_provider):
    router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])
    def task(database, kind, identifier):
        if kind == "assignment": return database.get_job(identifier)
        from ..services.learning.store import LearningStore
        return LearningStore(database).run(identifier)

    @router.get("/{kind}/{identifier}")
    async def metadata(kind: Literal["assignment", "learning"], identifier: str):
        database = database_provider()
        try:
            current = task(database, kind, identifier)
            trace = database.diagnostics.trace(kind, identifier)
        except KeyError as error: raise HTTPException(404, "任务不存在") from error
        except ValueError as error: raise HTTPException(400, str(error)) from error
        return {"schema_version": 1, "trace_id": identifier, "task_kind": kind, "state": current["status"],
                "capture_available": trace.available, "environment": getattr(trace, "manifest", {}).get("environment", {}),
                "storage_write_failed": bool(getattr(trace, "failed", False))}

    @router.post("/{kind}/{identifier}/export")
    async def export(kind: Literal["assignment", "learning"], identifier: str, payload: DiagnosticExport):
        try:
            data = await asyncio.to_thread(build_bundle, database_provider(), kind, identifier,
                include_content=payload.include_content, include_screenshots=payload.include_screenshots,
                renderer=payload.renderer.model_dump() if payload.renderer else {})
        except KeyError as error: raise HTTPException(404, "任务不存在") from error
        except (ValueError, OSError) as error: raise HTTPException(400, "诊断包导出失败：" + str(error)) from error
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="diagnostic-{kind}-{identifier[:16]}.zip"', "Cache-Control": "no-store"})

    return router


def install_request_diagnostics(app, database_provider, settings_provider=None):
    @app.exception_handler(StarletteHTTPException)
    async def rejected(request, error):
        trace = current_trace.get()
        if trace:
            trace.exception(error, phase="api", data={"http_status": error.status_code, "api_request_id": getattr(request.state, "diagnostic_id", None)})
        settings = settings_provider() if settings_provider else database_provider().settings
        logging.getLogger("answer-studio.api").warning("API rejected status=%s request_id=%s reason=%s", error.status_code,
            getattr(request.state, "diagnostic_id", None), task_scrubber(settings).text(error.detail))
        return await http_exception_handler(request, error)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        trace = current_trace.get()
        if trace: trace.record("api.validation_failed", "Request validation failed", {"api_request_id": getattr(request.state, "diagnostic_id", None),
            "errors": [{key: item.get(key) for key in ("loc", "msg", "type")} for item in error.errors()]}, component="api", level="error")
        return await request_validation_exception_handler(request, error)

    @app.middleware("http")
    async def request_diagnostics(request, call_next):
        identifier = uuid.uuid4().hex
        request.state.diagnostic_id = identifier
        started = time.monotonic()
        path = request.url.path
        match = re.match(r"^/api/(jobs|learning/runs)/([a-f0-9]{32})(?:/|$)", path)
        trace = database_provider().diagnostics.trace("assignment" if match[1] == "jobs" else "learning", match[2]) if match else None
        token = current_trace.set(trace)
        log_token = current_log_settings.set(database_provider().settings)
        try:
            response = await call_next(request)
            if request.method not in {"GET", "OPTIONS"} or response.status_code >= 400:
                data = {"method": request.method, "path": path, "http_status": response.status_code, "request_id": identifier, "duration_ms": round((time.monotonic() - started) * 1000)}
                if trace: trace.record("api.response", data=data, component="api", level="warning" if response.status_code >= 400 else "info")
                if not trace:
                    logging.getLogger("answer-studio.api").log(logging.WARNING if response.status_code >= 400 else logging.INFO,
                        "HTTP %s %s -> %s request_id=%s", request.method, path, response.status_code, identifier,
                        extra={"diagnostic_code": "api.response", "diagnostic_data": data})
            response.headers["X-Diagnostic-Request-ID"] = identifier
            return response
        except Exception as error:
            if trace: trace.exception(error, phase="api", data={"request_id": identifier, "method": request.method, "path": path})
            logging.getLogger("answer-studio.api").exception("API request failed request_id=%s", identifier)
            raise
        finally:
            current_trace.reset(token)
            current_log_settings.reset(log_token)
