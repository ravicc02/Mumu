from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cdp_use import CDPClient

from ..learning.live import LiveBrowserChannel


class ObserverConnection:
    """A viewer owns only its CDP attachment, never the Agent's browser/session."""

    def __init__(self, client, session_id):
        self.client, self.session_id = client, session_id
        self.listeners = {}
        client.register.Page.screencastFrame(self._frame)

    def _frame(self, event, session_id):
        handler = self.listeners.get("Page.screencastFrame")
        if handler and session_id == self.session_id:
            handler(event)

    def on(self, name, handler): self.listeners[name] = handler
    def remove_listener(self, name, handler): self.listeners.pop(name, None)

    async def send(self, method, params=None):
        return await asyncio.wait_for(self.client.send_raw(method, params, self.session_id), 8)

    async def detach(self):
        self.listeners.clear()
        await self.client.stop()


class AssignmentLiveBrowser:
    def __init__(self, session):
        self.session = session
        self.page = None
        self.live = LiveBrowserChannel(None, self, resolve_page=self.resolve_page)

    async def resolve_page(self):
        target = self.session.get_focused_target()
        if target is None: raise RuntimeError("任务当前没有可操作的页面")
        info = {"targetId": target.target_id, "url": target.url}
        if self.page is None or self.page.target_id != info["targetId"]:
            self.page = SimpleNamespace(target_id=info["targetId"], url=info["url"], is_closed=lambda: self.live.closed)
        self.page.url = info["url"]
        return self.page, self

    async def new_cdp_session(self, page):
        client = CDPClient(self.session.cdp_url)
        try:
            await asyncio.wait_for(client.start(), 8)
            result = await asyncio.wait_for(client.send.Target.attachToTarget({"targetId": page.target_id, "flatten": True}), 8)
            return ObserverConnection(client, result["sessionId"])
        except BaseException:
            await client.stop()
            raise
