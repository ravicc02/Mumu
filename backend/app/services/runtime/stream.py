from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger("mumu.browser")
VIEWER_ORIGINS = {"http://127.0.0.1:5174", "http://localhost:5174", "http://127.0.0.1:8877", "file://", "null"}


class BrowserTickets:
    def __init__(self): self.tickets = {}

    def issue(self, identifier):
        now = time.monotonic()
        self.tickets = {key: value for key, value in self.tickets.items() if value[1] > now}
        if len(self.tickets) >= 256: self.tickets.pop(next(iter(self.tickets)))
        token = secrets.token_urlsafe(32)
        self.tickets[token] = (identifier, now + 30)
        return {"token": token}

    def consume(self, identifier, token):
        ticket = self.tickets.pop(token, None)
        return bool(ticket and ticket[0] == identifier and ticket[1] > time.monotonic())


async def serve_browser(websocket: WebSocket, identifier, authorize, get_live, on_error=None):
    if websocket.headers.get("origin") not in VIEWER_ORIGINS:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    tasks, queue, live = [], None, None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), 5)
        if len(raw) > 1024: raise ValueError("浏览器凭证过长")
        auth = json.loads(raw)
        if not isinstance(auth, dict) or not authorize(identifier, str(auth.get("token", ""))):
            await websocket.close(code=1008)
            return
        live = get_live(identifier)
        queue = await asyncio.wait_for(live.subscribe(), 20)

        async def send_frames():
            while True:
                frame = await queue.get()
                await websocket.send_json(frame)
                if frame["type"] == "closed": return

        async def receive_input():
            while True:
                raw = await websocket.receive_text()
                if len(raw) > 20000: raise ValueError("浏览器输入过长")
                if await live.dispatch(json.loads(raw)) is False:
                    await websocket.send_json({"type":"input_unavailable","message":"页面正在切换，请等待新画面后重新操作"})

        tasks = [asyncio.create_task(send_frames()), asyncio.create_task(receive_input())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done: task.result()
    except WebSocketDisconnect:
        pass
    except (ValueError,asyncio.TimeoutError) as error:
        logger.warning("Browser connection rejected by protocol validation: %s",type(error).__name__)
        if websocket.application_state == WebSocketState.CONNECTED and websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close(code=1008)
    except Exception as error:
        if on_error: on_error(error)
        logger.exception("Live browser stream ended", extra={"job_id": identifier})
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({"type": "closed", "message": "浏览器连接失败，请重新连接或导出诊断包"})
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if live and queue:
            try: await live.unsubscribe(queue)
            except Exception: logger.exception("Detaching live browser failed")
        if websocket.application_state == WebSocketState.CONNECTED and websocket.client_state == WebSocketState.CONNECTED:
            await websocket.close()
