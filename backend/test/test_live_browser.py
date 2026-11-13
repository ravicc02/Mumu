from __future__ import annotations

import asyncio
import base64
import socket
import tempfile
import hashlib
import io
from PIL import Image
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from cdp_use import CDPClient
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from app.services.tasks.live import AssignmentLiveBrowser
from app.services.runtime.browser import chrome_executable
from app.services.runtime.stream import BrowserTickets, serve_browser
from app.services.learning.live import BrowserInput, LiveBrowserChannel


class BrowserAccessTests(unittest.TestCase):
    def test_tickets_expire_bind_to_one_task_and_cannot_be_replayed(self):
        store = BrowserTickets()
        token = store.issue("one")["token"]
        self.assertFalse(store.consume("two", token))
        self.assertFalse(store.consume("one", token))
        token = store.issue("one")["token"]
        self.assertTrue(store.consume("one", token))
        self.assertFalse(store.consume("one", token))
        with patch("app.services.runtime.stream.time.monotonic", return_value=10):
            token = store.issue("one")["token"]
        with patch("app.services.runtime.stream.time.monotonic", return_value=41):
            self.assertFalse(store.consume("one", token))

    def test_untrusted_origins_and_invalid_tokens_never_attach_to_browser(self):
        app, attached = FastAPI(), []
        @app.websocket("/browser")
        async def route(ws: WebSocket):
            await serve_browser(ws, "job", lambda *_: False, lambda _: attached.append(True))
        client = TestClient(app)
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/browser", headers={"origin": "https://untrusted.test"}): pass
        with client.websocket_connect("/browser", headers={"origin": "http://127.0.0.1:5174"}) as ws:
            ws.send_json({"token": "invalid"})
            with self.assertRaises(WebSocketDisconnect): ws.receive_json()
        self.assertEqual(attached, [])

    def test_input_rejects_nonfinite_coordinates_and_arbitrary_commands(self):
        for payload in ({"type": "wheel", "x": float("nan")}, {"type": "evaluate", "script": "anything"}, {"type": "text", "text": "a" * 10001}):
            with self.assertRaises(ValueError): BrowserInput.model_validate(payload)


class AssignmentLiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_switch_rejects_stale_input_and_waits_for_first_frame(self):
        from unittest.mock import AsyncMock
        client=SimpleNamespace(send=AsyncMock())
        channel=LiveBrowserChannel(SimpleNamespace(),SimpleNamespace())
        channel.client=client
        self.assertFalse(await channel.dispatch({"type":"text","text":"old input"}))
        channel.last_frame={"width":1280,"height":800}
        channel.view_id=2
        self.assertFalse(await channel.dispatch({"type":"text","text":"old input","view_id":1}))
        client.send.assert_not_awaited()
        self.assertTrue(await channel.dispatch({"type":"text","text":"current input","view_id":2}))
        client.send.assert_awaited_once_with("Input.insertText",{"text":"current input"})

    async def test_cancelled_subscription_does_not_leave_an_orphan_viewer(self):
        async def blocked_attach(page): await asyncio.Event().wait()
        channel=LiveBrowserChannel(SimpleNamespace(is_closed=lambda:False),SimpleNamespace(new_cdp_session=blocked_attach))
        with self.assertRaises(TimeoutError): await asyncio.wait_for(channel.subscribe(),.02)
        self.assertEqual(len(channel.subscribers),0)

    async def test_headed_background_window_keeps_delivering_changed_frames(self):
        from app.services.tasks.runner import _launch_chrome, _terminate_process
        with tempfile.TemporaryDirectory() as folder:
            process, endpoint = await _launch_chrome(folder,headless=False,timeout=15)
            viewer = browser = cover = None
            try:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.connect_over_cdp(endpoint)
                    context = browser.contexts[0]
                    page = context.pages[0]
                    await page.set_content('<body style="height:4000px;margin:0;background:linear-gradient(#f00,#00f)"><input style="position:fixed;top:30px;left:30px" value="original"><div id="tick">first</div></body>')
                    client = await context.new_cdp_session(page)
                    target = (await client.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
                    session = SimpleNamespace(cdp_url=endpoint,get_focused_target=lambda:SimpleNamespace(target_id=target,url=page.url))
                    viewer = AssignmentLiveBrowser(session)
                    queue = await viewer.live.subscribe()
                    initial = await asyncio.wait_for(queue.get(),8)
                    cover = await playwright.chromium.launch(headless=False,executable_path=str(chrome_executable()),args=["--start-maximized"])
                    covering_page = await cover.new_page()
                    await covering_page.set_content('<h1>Background rendering verification</h1>')
                    await covering_page.bring_to_front()
                    await asyncio.sleep(1)
                    before = hashlib.sha256(initial["image"].encode()).hexdigest()
                    await viewer.live.dispatch({"type":"wheel","x":.5,"y":.5,"delta_y":1000})
                    await page.wait_for_function("scrollY>100")
                    deadline = asyncio.get_running_loop().time()+8
                    while True:
                        current = await asyncio.wait_for(queue.get(),max(.1,deadline-asyncio.get_running_loop().time()))
                        if hashlib.sha256(current["image"].encode()).hexdigest()!=before: break
                    window = await client.send("Browser.getWindowForTarget", {"targetId":target})
                    await client.send("Browser.setWindowBounds", {"windowId":window["windowId"],"bounds":{"windowState":"minimized"}})
                    await page.evaluate("document.body.style.background='rgb(0,180,60)'")
                    deadline=asyncio.get_running_loop().time()+8
                    while True:
                        changed=await asyncio.wait_for(queue.get(),max(.1,deadline-asyncio.get_running_loop().time()))
                        image=Image.open(io.BytesIO(base64.b64decode(changed["image"]))).convert("RGB")
                        red,green,blue=image.getpixel((image.width//2,image.height//2))
                        if green>red+30 and green>blue+30: break
                    self.assertNotEqual(changed["image"],current["image"])
                    await viewer.live.unsubscribe(queue)
                    self.assertFalse(page.is_closed())
                    self.assertGreater(await page.evaluate("scrollY"),100)
                    await viewer.live.close(); viewer=None
                    await cover.close(); cover=None
                    await browser.close(); browser=None
            finally:
                if viewer:
                    try: await viewer.live.close()
                    except Exception: pass
                await _terminate_process(process)

    async def test_original_page_shared_by_two_viewers_scroll_input_tab_switch_and_detach(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()), args=[f"--remote-debugging-port={port}"])
            main, viewer = None, None
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                page = await context.new_page()
                await page.set_content('<input style="position:fixed;left:20px;top:20px;width:200px;height:40px"><div style="height:4000px">Shared original page</div>')
                probe = await context.new_cdp_session(page)
                target = (await probe.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
                async with httpx.AsyncClient(trust_env=False) as client:
                    endpoint = (await client.get(f"http://127.0.0.1:{port}/json/version")).json()["webSocketDebuggerUrl"]
                main = CDPClient(endpoint)
                await main.start()
                sid = (await main.send.Target.attachToTarget({"targetId": target, "flatten": True}))["sessionId"]
                active = SimpleNamespace(target_id=target, url=page.url)
                session = SimpleNamespace(cdp_url=endpoint, get_focused_target=lambda: active)
                viewer = AssignmentLiveBrowser(session)
                first, second = await viewer.live.subscribe(), await viewer.live.subscribe()
                frame = await asyncio.wait_for(first.get(), 5)
                other = await asyncio.wait_for(second.get(), 5)
                self.assertEqual(frame["image"], other["image"])
                self.assertEqual((frame["width"], frame["height"]), (1280, 800))
                await viewer.live.dispatch({"type": "pointer", "action": "mousePressed", "button": "left", "x": .05, "y": .05})
                await viewer.live.dispatch({"type": "pointer", "action": "mouseReleased", "button": "left", "x": .05, "y": .05})
                await viewer.live.dispatch({"type": "text", "text": "shared input"})
                self.assertEqual(await page.locator("input").input_value(), "shared input")
                await viewer.live.dispatch({"type": "wheel", "x": .5, "y": .5, "delta_y": 650})
                await page.wait_for_function("scrollY > 0")
                changed = await asyncio.wait_for(first.get(), 5)
                while changed["image"] == frame["image"]:
                    changed = await asyncio.wait_for(first.get(), 5)
                self.assertNotEqual(changed["image"], frame["image"])
                new_page = await context.new_page()
                await new_page.set_content("<h1>New Agent tab</h1>")
                probe2 = await context.new_cdp_session(new_page)
                target2 = (await probe2.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
                active.target_id, active.url = target2, new_page.url
                for _ in range(20):
                    if viewer.live.page.target_id == target2 and viewer.live.last_frame: break
                    await asyncio.sleep(.1)
                self.assertEqual(viewer.live.page.target_id, target2)
                self.assertIsNotNone(viewer.live.last_frame)
                await viewer.live.unsubscribe(first)
                self.assertIsNotNone(viewer.live.client)
                await viewer.live.unsubscribe(second)
                self.assertIsNone(viewer.live.client)
                self.assertFalse(page.is_closed())
                self.assertEqual(await page.locator("input").input_value(), "shared input")
            finally:
                if viewer: await viewer.live.close()
                if main: await main.stop()
                await browser.close()
