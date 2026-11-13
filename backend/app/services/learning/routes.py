from __future__ import annotations

import asyncio
from pathlib import Path
import hashlib

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from ..platforms.catalog import CATALOG, platform_spec, platform_url, require_business
from ..browser_stream import serve_browser


class CreateLearningRun(BaseModel):
    course_id: str


class AddLearningCourse(BaseModel):
    account_id: str
    url: str = Field(min_length=8,max_length=4096)
    name: str = Field(min_length=1,max_length=200)


def learning_router(service, guard_idle, guard_start, collection_lock):
    router = APIRouter(prefix="/api/learning", tags=["learning"])

    def learning_account(account_id):
        try: account = service._database().get_account(account_id)
        except KeyError as error: raise HTTPException(404, str(error)) from error
        try: require_business(account["platform"], "learning")
        except ValueError as error: raise HTTPException(409, str(error)) from error
        return account

    @router.get("/capabilities")
    async def capabilities():
        return {"engine": "ocs-5.4.0-resources.2", "local": True, "platforms": [name for name,spec in CATALOG.items() if spec["learning"]],
                "automatic": ["video", "audio"], "manual": ["quiz", "verification", "unknown"],
                "resource_handlers": {"chaoxing": ["video", "audio", "document", "book", "audio_ppt", "hyperlink", "timed_reading"]},
                "external_ocs_required": False, "userscript_manager_required": False}

    @router.get("/courses")
    async def courses(account_id: str, academic_year: int = 2026):
        account = learning_account(account_id)
        if not platform_spec(account["platform"])["academic_year_filter"]: academic_year = 0
        result = service.store.courses(account_id, academic_year)
        runs = service.store.runs(account_id)
        for course in result:
            latest = next((run for run in runs if run["course_id"] == course["id"]), None)
            course["last_run"] = service.payload(latest) if latest else None
        return result

    @router.post("/accounts/{account_id}/collect")
    async def collect(account_id: str, academic_year: int = 2026):
        learning_account(account_id)
        guard_idle(account_id)
        if academic_year != 0 and not 2000 <= academic_year <= 2100: raise HTTPException(422, "开课年份无效")
        try:
            account = service._database().get_account(account_id)
            if not platform_spec(account["platform"])["learning"]: raise RuntimeError("该平台请在作业页面处理课堂实验。")
            if account["status"] != "ready": raise RuntimeError("账号尚未授权，请先刷新 Cookie")
            async with collection_lock(account_id):
                result = await service.collect(account_id, academic_year)
            return {"courses": result, "count": len(result)}
        except KeyError as error: raise HTTPException(404, str(error)) from error
        except RuntimeError as error: raise HTTPException(409, str(error)) from error

    @router.get("/runs")
    async def runs(account_id: str | None = None):
        if account_id: learning_account(account_id)
        return [service.payload(run) for run in service.store.runs(account_id)]

    @router.post("/courses")
    async def add_course(payload: AddLearningCourse):
        try:
            account = learning_account(payload.account_id)
            platform_url(account["platform"], payload.url)
            if platform_spec(account["platform"])["academic_year_filter"]:
                raise ValueError("学习通课程请通过按开课年份采集添加")
            if not payload.name.strip(): raise ValueError("课程名称不能为空")
            identity = hashlib.sha256(payload.url.encode()).hexdigest()[:24]
            courses = service.store.upsert_courses(account["id"], [{"courseId":identity,"classId":"","courseName":payload.name.strip(),"url":payload.url}],0,replace=False)
            return next(item for item in courses if item["upstream_id"]==identity)
        except KeyError as error: raise HTTPException(404,str(error)) from error
        except ValueError as error: raise HTTPException(422,str(error)) from error

    @router.post("/runs")
    async def create_run(payload: CreateLearningRun):
        try:
            course = service.store.course(payload.course_id)
            learning_account(course["account_id"])
            guard_start(course["account_id"])
            return service.start(course["id"])
        except KeyError as error: raise HTTPException(404, str(error)) from error
        except RuntimeError as error: raise HTTPException(409, str(error)) from error

    @router.get("/runs/{identifier}")
    async def get_run(identifier: str):
        try: return service.payload(service.store.run(identifier))
        except KeyError as error: raise HTTPException(404, str(error)) from error

    @router.delete("/runs/{identifier}")
    async def delete_task(identifier: str):
        try: return service.delete_task(identifier)
        except KeyError as error: raise HTTPException(404, str(error)) from error
        except RuntimeError as error: raise HTTPException(409, str(error)) from error

    @router.post("/runs/{identifier}/{action}")
    async def action(identifier: str, action: str):
        try:
            if action == "retry":
                account_id = service.store.run(identifier)["account_id"]
                learning_account(account_id)
                guard_start(account_id)
            return await service.control(identifier, action)
        except KeyError as error: raise HTTPException(404, str(error)) from error
        except RuntimeError as error: raise HTTPException(409, str(error)) from error

    @router.get("/runs/{identifier}/events")
    async def events(identifier: str, after_id: int = 0):
        try: return service.store.events(identifier, after_id)
        except KeyError as error: raise HTTPException(404, str(error)) from error

    @router.get("/runs/{identifier}/snapshot")
    async def snapshot(identifier: str):
        try: return await service.snapshot(identifier)
        except RuntimeError as error: raise HTTPException(409, str(error)) from error

    @router.post("/runs/{identifier}/browser/session")
    async def browser_session(identifier: str):
        try: return service.browser_ticket(identifier)
        except RuntimeError as error: raise HTTPException(409, str(error)) from error

    @router.websocket("/runs/{identifier}/browser")
    async def live_browser(websocket: WebSocket, identifier: str):
        def get_live(identifier):
            browser=service.browsers.get(identifier)
            if not browser: raise RuntimeError("当前任务浏览器已关闭")
            return browser.live
        trace=service._database().diagnostics.trace("learning",identifier)
        await serve_browser(websocket,identifier,service.authorize_browser,get_live,
                            on_error=lambda error:trace.exception(error,phase="live_browser"))

    @router.get("/runs/{identifier}/artifacts")
    async def artifacts(identifier: str):
        try: return service.store.artifacts(identifier)
        except KeyError as error: raise HTTPException(404, str(error)) from error

    @router.get("/artifacts/{artifact_id}")
    async def artifact(artifact_id: str):
        with service._database().connect() as connection:
            row = connection.execute("SELECT path FROM learning_artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row or not Path(row["path"]).is_file(): raise HTTPException(404, "网课截图不存在")
        return FileResponse(row["path"])

    @router.websocket("/runs/{identifier}/stream")
    async def stream(websocket: WebSocket, identifier: str, after_id: int = 0):
        await websocket.accept()
        channel = f"learning:{identifier}"
        queue = service.hub.subscribe(channel)
        disconnected = None
        waiting = None
        async def disconnect():
            while (await websocket.receive())["type"] != "websocket.disconnect": pass
        try:
            try: history = service.store.events(identifier, after_id)
            except KeyError:
                await websocket.close(code=1008); return
            disconnected = asyncio.create_task(disconnect())
            cursor = after_id
            for event in history:
                await websocket.send_json(event); cursor = event["id"]
            while True:
                waiting = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({waiting, disconnected}, return_when=asyncio.FIRST_COMPLETED)
                if disconnected in done: break
                event = waiting.result()
                if event.get("id", cursor + 1) <= cursor: continue
                await websocket.send_json(event)
                cursor = event.get("id", cursor)
        except WebSocketDisconnect: pass
        finally:
            tasks = [task for task in (waiting, disconnected) if task]
            for task in tasks:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            service.hub.unsubscribe(channel, queue)

    return router
