from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BrowserInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type: Literal["pointer", "wheel", "key", "text"]
    action: Literal["mousePressed", "mouseReleased", "mouseMoved", "keyDown", "keyUp"] = "mouseMoved"
    x: float = Field(default=0, ge=0, le=1)
    y: float = Field(default=0, ge=0, le=1)
    button: Literal["none", "left", "middle", "right"] = "none"
    buttons: int = Field(default=0, ge=0, le=7)
    delta_x: float = Field(default=0, ge=-2000, le=2000)
    delta_y: float = Field(default=0, ge=-2000, le=2000)
    key: str = Field(default="", max_length=32)
    code: str = Field(default="", max_length=32)
    key_code: int = Field(default=0, ge=0, le=255)
    modifiers: int = Field(default=0, ge=0, le=15)
    text: str = Field(default="", max_length=10000)
    view_id: int | None = Field(default=None, ge=0)


class LiveBrowserChannel:
    """One Chrome screencast shared by every viewer of the same task page."""

    def __init__(self, page, context, *, resolve_page=None):
        self.page, self.context = page, context
        self.resolve_page = resolve_page
        self.watcher = None
        self.client = None
        self.subscribers = set()
        self.lock = asyncio.Lock()
        self.pending = set()
        self.last_frame = None
        self.closed = False
        self.view_id = 0

    def _put(self, queue, payload):
        if queue.full(): queue.get_nowait()
        queue.put_nowait(payload)

    def _frame(self, event):
        client = self.client
        if not client or self.closed: return
        task = asyncio.create_task(self._ack(client, event["sessionId"]))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        metadata = event["metadata"]
        self.last_frame = {
            "type": "frame", "image": event["data"], "url": self.page.url,
            "width": metadata["deviceWidth"], "height": metadata["deviceHeight"], "view_id": self.view_id,
        }
        for queue in self.subscribers: self._put(queue, self.last_frame)

    async def _ack(self, client, identifier):
        try: await client.send("Page.screencastFrameAck", {"sessionId": identifier})
        except Exception:
            logging.getLogger("answer-studio.browser").exception("Acknowledging browser frame failed")
            if not self.closed and self.client is client:
                for queue in self.subscribers:
                    self._put(queue, {"type": "closed", "message": "浏览器画面连接已断开"})

    async def subscribe(self):
        async with self.lock:
            if self.resolve_page and not self.client:
                self.page, self.context = await self.resolve_page()
            if self.closed or self.page.is_closed(): raise RuntimeError("任务浏览器已关闭")
            queue = asyncio.Queue(maxsize=1)
            self.subscribers.add(queue)
            if self.last_frame: self._put(queue, self.last_frame)
            if not self.client:
                try:
                    await self._attach()
                except BaseException:
                    self.subscribers.discard(queue)
                    raise
                if self.resolve_page:
                    self.watcher = asyncio.create_task(self._follow_page())
            return queue

    async def _attach(self):
        client = await self.context.new_cdp_session(self.page)
        self.view_id += 1
        self.client = client
        client.on("Page.screencastFrame", self._frame)
        try:
            await client.send("Page.startScreencast", {
                "format": "jpeg", "quality": 75, "maxWidth": 1920,
                "maxHeight": 1200, "everyNthFrame": 1,
            })
        except BaseException:
            await self._detach()
            raise

    async def _follow_page(self):
        try:
            while True:
                await asyncio.sleep(.5)
                async with self.lock:
                    page, context = await self.resolve_page()
                    if page is not self.page:
                        for queue in self.subscribers:
                            self._put(queue, {"type":"switching", "message":"正在切换任务页面"})
                        await self._detach()
                        self.page, self.context = page, context
                        await self._attach()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger("answer-studio.browser").exception("Following Agent page failed")
            for queue in self.subscribers:
                self._put(queue, {"type": "closed", "message": "任务页面连接已断开，请重新连接"})

    async def _stop_watcher(self):
        if self.watcher:
            self.watcher.cancel()
            await asyncio.gather(self.watcher, return_exceptions=True)
            self.watcher = None

    async def unsubscribe(self, queue):
        async with self.lock:
            self.subscribers.discard(queue)
            if not self.subscribers:
                await self._stop_watcher()
                await self._detach()

    async def _detach(self):
        client, self.client = self.client, None
        self.last_frame = None
        if client:
            client.remove_listener("Page.screencastFrame", self._frame)
            try: await client.send("Page.stopScreencast")
            finally: await client.detach()

    async def close(self):
        async with self.lock:
            self.closed = True
            await self._stop_watcher()
            for queue in self.subscribers:
                self._put(queue, {"type": "closed", "message": "任务浏览器已关闭"})
            await self._detach()
        if self.pending: await asyncio.gather(*self.pending, return_exceptions=True)

    async def dispatch(self, payload):
        event = BrowserInput.model_validate(payload)
        async with self.lock:
            client = self.client
            if not client or not self.last_frame or self.closed:
                return False
            if event.view_id is not None and event.view_id != self.view_id:
                return False
            if event.type == "text":
                await client.send("Input.insertText", {"text": event.text})
            elif event.type == "key":
                if event.action not in {"keyDown", "keyUp"}: raise ValueError("键盘动作无效")
                await client.send("Input.dispatchKeyEvent", {
                    "type": event.action, "key": event.key, "code": event.code,
                    "windowsVirtualKeyCode": event.key_code, "modifiers": event.modifiers,
                })
            else:
                if event.type == "pointer" and event.action not in {"mousePressed", "mouseReleased", "mouseMoved"}:
                    raise ValueError("鼠标动作无效")
                params = {
                    "type": "mouseWheel" if event.type == "wheel" else event.action,
                    "x": min(event.x, .9999) * self.last_frame["width"],
                    "y": min(event.y, .9999) * self.last_frame["height"],
                    "button": event.button, "buttons": event.buttons, "modifiers": event.modifiers,
                }
                if event.type == "wheel": params.update(deltaX=event.delta_x, deltaY=event.delta_y)
                elif event.action != "mouseMoved": params["clickCount"] = 1
                await client.send("Input.dispatchMouseEvent", params)
            return True
