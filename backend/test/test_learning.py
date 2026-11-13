from __future__ import annotations

import asyncio
import base64
import io
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import httpx
from playwright.async_api import async_playwright

from app.config import Settings
from app.db import Database
from app.events import EventHub
from app.services.browser import chrome_executable
from app.services.learning.browser import LearningBrowser, RUNTIME
from app.services.learning.routes import learning_router
from app.services.learning.service import LearningService
from app.services.learning.store import LearningStore
from app.services.learning.live import BrowserInput


class FakeLearningBrowser:
    instances = []
    def __init__(self, account, **kwargs):
        self.account = account
        self.finished = False
        self.closed = False
        self.played = False
        self.paused = False
        self.ticks = 0
        self.__class__.instances.append(self)
    async def open(self): pass
    async def close(self): self.closed = True
    async def navigate(self, url): pass
    async def open_chapter(self, identifier): pass
    async def verify_catalog(self): return [{"chapterId": "chapter", "title": "测试章节", "remaining": 0 if self.finished else 1, "completed": self.finished}]
    async def play(self, frame): self.played = True; self.paused = False; return {"started": True}
    async def pause(self): self.paused = True
    async def inspect(self):
        self.ticks += 1
        return {"unit": {"id": "unit", "title": "测试视频", "mandatory": True, "completed": False}}, [{"job_id": "unit", "frame": self, "data": {"challenge": False, "quiz": False, "error": "", "media": {"paused": self.paused or not self.played, "ended": False, "position": self.ticks, "duration": 120}}}]
    async def focus(self): pass
    async def snapshot(self): return {"image": "data:image/png;base64,cG5n", "url": "https://example.test/course"}
    async def evidence(self):
        yield {"index": 1, "count": 1, "image": b"png", "url": "https://example.test/course"}


class DisconnectLearningBrowser(FakeLearningBrowser):
    def __init__(self, account, **kwargs):
        super().__init__(account, **kwargs)
        self.headless = kwargs.get("headless", True)
        self.connection_lost = asyncio.Event()
        self.close_reason = "浏览器窗口已关闭，任务已中断，请重试。"
        self.failure_kind = "context_closed"
        self.focus = AsyncMock()

    def lose_connection(self):
        self.connection_lost.set()


class AutoplayBlockedBrowser(FakeLearningBrowser):
    def __init__(self, account, **kwargs):
        super().__init__(account, **kwargs)
        self.play_calls = self.pause_calls = self.chapter_calls = self.navigate_calls = 0
        self.user_started_during_play = False
    async def navigate(self, url): self.navigate_calls += 1
    async def open_chapter(self, identifier): self.chapter_calls += 1
    async def play(self, frame):
        self.play_calls += 1
        if self.user_started_during_play:
            self.played, self.paused = True, False
        return {"started": False, "reason": "Autoplay blocked"}
    async def pause(self):
        self.pause_calls += 1
        await super().pause()


class LearningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.root.name))
        self.settings.ensure_directories()
        self.db = Database(self.settings); self.db.initialize()
        self.account = self.db.create_account("test", "chaoxing", self.settings)
        self.db.update_account(self.account["id"], status="ready")
        self.service = LearningService(self.db, self.settings, EventHub(), FakeLearningBrowser)
        self.service.initialize()
        self.course = self.service.store.upsert_courses(self.account["id"], [{"courseId": "c", "classId": "class", "courseName": "测试网课", "url": "https://example.test/course"}], 2026)[0]
        self.notify = patch("app.services.learning.service.send_completion_notification", AsyncMock(return_value={}))
        self.notify.start()

    async def asyncTearDown(self):
        await self.service.shutdown()
        self.notify.stop()
        self.root.cleanup()

    async def wait_until(self, predicate, timeout=20):
        async with asyncio.timeout(timeout):
            while not predicate(): await asyncio.sleep(.05)

    async def test_continue_adopts_manual_play_without_pausing_replaying_or_reopening(self):
        self.service.browser_factory = AutoplayBlockedBrowser
        run = self.service.start(self.course["id"])
        identifier = run["id"]
        await self.wait_until(lambda: self.service.store.run(identifier)["status"] == "awaiting_user")
        browser = self.service.browsers[identifier]
        self.assertEqual(browser.pause_calls, 0)
        browser.played, browser.paused = True, False
        previous = browser.ticks
        await self.service.control(identifier, "continue")
        await self.wait_until(lambda: any(event["kind"] == "media_started" for event in self.service.store.events(identifier)))
        await self.wait_until(lambda: browser.ticks >= previous + 2)
        self.assertEqual(self.service.store.run(identifier)["status"], "running")
        self.assertFalse(browser.paused)
        self.assertEqual((browser.play_calls, browser.pause_calls, browser.chapter_calls, browser.navigate_calls), (3, 0, 1, 1))
        event = next(item for item in self.service.store.events(identifier) if item["kind"] == "media_started")
        self.assertTrue(event["data"]["adopted"])
        self.assertGreater(self.service.store.run(identifier)["position"], previous)

    async def test_transient_autoplay_failure_recovers_without_manual_continue(self):
        class TransientBrowser(AutoplayBlockedBrowser):
            async def play(self, frame):
                self.play_calls += 1
                if self.play_calls == 1: return {"started": False, "reason": "play interrupted by pause"}
                self.played, self.paused = True, False
                return {"started": True}
        self.service.browser_factory = TransientBrowser
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: any(event["kind"] == "media_started" for event in self.service.store.events(run["id"])))
        browser = self.service.browsers[run["id"]]
        self.assertEqual(browser.play_calls, 2)
        self.assertEqual(browser.pause_calls, 0)
        self.assertEqual(self.service.store.run(run["id"])["status"], "running")
        self.assertFalse(any(event["kind"] == "awaiting_user" for event in self.service.store.events(run["id"])))

    async def test_accidental_pause_recovers_but_manual_task_pause_stays_paused(self):
        self.service.browser_factory = AutoplayBlockedBrowser
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: run["id"] in self.service.browsers)
        browser = self.service.browsers[run["id"]]
        browser.play = AsyncMock(side_effect=lambda frame: self._successful_play(browser))
        await self.wait_until(lambda: browser.played)
        browser.paused = True
        await self.wait_until(lambda: browser.play.await_count >= 2)
        self.assertFalse(browser.paused)
        self.assertEqual(self.service.store.run(run["id"])["status"], "running")
        await self.service.control(run["id"], "pause")
        calls = browser.play.await_count
        await asyncio.sleep(1.2)
        self.assertTrue(browser.paused)
        self.assertEqual(browser.play.await_count, calls)
        self.assertEqual(self.service.store.run(run["id"])["status"], "paused")

    @staticmethod
    def _successful_play(browser):
        browser.played, browser.paused = True, False
        return {"started": True}

    async def test_immediate_pause_after_each_start_has_a_bounded_recovery_budget(self):
        class FlappingBrowser(AutoplayBlockedBrowser):
            async def play(self, frame):
                self.play_calls += 1
                self.played, self.paused = True, False
                return {"started": True}
            async def inspect(self):
                self.paused = True
                return await super().inspect()
        self.service.browser_factory = FlappingBrowser
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: self.service.store.run(run["id"])["status"] == "awaiting_user", timeout=6)
        browser = self.service.browsers[run["id"]]
        self.assertEqual(browser.play_calls, 3)
        self.assertIn("3 次", self.service.store.run(run["id"])["error"])
        self.assertEqual(browser.pause_calls, 0)

    async def test_stopping_during_autoplay_recovery_cancels_remaining_attempts(self):
        self.service.browser_factory = AutoplayBlockedBrowser
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: run["id"] in self.service.browsers and self.service.browsers[run["id"]].play_calls >= 1)
        browser = self.service.browsers[run["id"]]
        await self.service.control(run["id"], "stop")
        await self.wait_until(lambda: run["id"] not in self.service.tasks)
        self.assertLess(browser.play_calls, 3)
        self.assertEqual(self.service.store.run(run["id"])["status"], "stopped")

    async def test_failed_autoplay_does_not_stop_play_started_during_the_request(self):
        self.service.browser_factory = AutoplayBlockedBrowser
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: self.service.store.run(run["id"])["status"] == "awaiting_user")
        browser = self.service.browsers[run["id"]]
        browser.user_started_during_play = True
        await self.service.control(run["id"], "continue")
        await self.wait_until(lambda: any(item["kind"] == "media_started" for item in self.service.store.events(run["id"])))
        self.assertTrue(browser.played)
        self.assertFalse(browser.paused)
        self.assertEqual(browser.pause_calls, 0)
        self.assertEqual(browser.play_calls, 4)
        self.assertEqual(self.service.store.run(run["id"])["status"], "running")

    async def test_continue_without_playback_still_waits_and_manual_pause_still_pauses(self):
        self.service.browser_factory = AutoplayBlockedBrowser
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: self.service.store.run(run["id"])["status"] == "awaiting_user")
        browser = self.service.browsers[run["id"]]
        await self.service.control(run["id"], "continue")
        await self.wait_until(lambda: browser.play_calls == 6 and self.service.store.run(run["id"])["status"] == "awaiting_user")
        self.assertEqual(browser.pause_calls, 0)
        browser.played, browser.paused = True, False
        await self.service.control(run["id"], "continue")
        await self.wait_until(lambda: any(item["kind"] == "media_started" for item in self.service.store.events(run["id"])))
        await self.service.control(run["id"], "pause")
        self.assertEqual(self.service.store.run(run["id"])["status"], "paused")
        self.assertEqual(browser.pause_calls, 1)
        self.assertTrue(browser.paused)

    async def test_pause_resume_stop_and_duplicate_course_guard(self):
        run = self.service.start(self.course["id"])
        with self.assertRaisesRegex(RuntimeError, "已有"): self.service.start(self.course["id"])
        await self.wait_until(lambda: run["id"] in self.service.browsers and self.service.browsers[run["id"]].played)
        browser = self.service.browsers[run["id"]]
        await self.service.control(run["id"], "pause")
        self.assertTrue(browser.paused)
        self.assertEqual(self.service.store.run(run["id"])["status"], "paused")
        await asyncio.sleep(.05)
        self.assertTrue(browser.paused)
        await self.service.control(run["id"], "resume")
        await self.wait_until(lambda: not browser.paused)
        await self.service.control(run["id"], "stop")
        await self.wait_until(lambda: run["id"] not in self.service.tasks)
        self.assertEqual(self.service.store.run(run["id"])["status"], "stopped")
        self.assertTrue(browser.closed)
        retry = await self.service.control(run["id"], "retry")
        self.assertEqual(retry["attempt"], 2)

    async def test_new_learning_tasks_default_to_headless_and_viewing_does_not_pause_or_focus(self):
        self.service.browser_factory = DisconnectLearningBrowser
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: run["id"] in self.service.browsers and self.service.browsers[run["id"]].played)
        browser = self.service.browsers[run["id"]]
        current = self.service.payload(self.service.store.run(run["id"]))
        self.assertEqual(current["headless"], 1)
        self.assertTrue(browser.headless)
        self.assertIn("view_browser", current["actions"])
        self.assertNotIn("open_browser", current["actions"])
        viewed = await self.service.control(run["id"], "view_browser")
        self.assertEqual(viewed["status"], "running")
        self.assertFalse(browser.paused)
        browser.focus.assert_not_awaited()

    async def test_browser_loss_interrupts_running_and_manually_paused_tasks(self):
        self.service.browser_factory = DisconnectLearningBrowser
        for paused in (False, True):
            with self.subTest(manually_paused=paused):
                run = self.service.start(self.course["id"])
                await self.wait_until(lambda: run["id"] in self.service.browsers and self.service.browsers[run["id"]].played)
                browser = self.service.browsers[run["id"]]
                if paused: await self.service.control(run["id"], "pause")
                browser.lose_connection()
                self.assertFalse(self.service.payload(self.service.store.run(run["id"]))["browser_available"])
                await self.wait_until(lambda: run["id"] not in self.service.tasks)
                result = self.service.store.run(run["id"])
                self.assertEqual(result["status"], "interrupted")
                self.assertIn("窗口已关闭", result["error"])
                self.assertTrue(any(event["kind"] == "browser_closed" for event in self.service.store.events(run["id"])))
                self.assertEqual(sum(event["kind"] == "paused" for event in self.service.store.events(run["id"])), int(paused))

    async def test_browser_loss_during_human_intervention_is_not_left_waiting_forever(self):
        self.service.browser_factory = DisconnectLearningBrowser
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: run["id"] in self.service.browsers and self.service.browsers[run["id"]].played)
        browser = self.service.browsers[run["id"]]
        browser.paused = True
        browser.play = AsyncMock(return_value={"started": False, "reason": "Playback remains blocked"})
        await self.wait_until(lambda: self.service.store.run(run["id"])["status"] == "awaiting_user")
        browser.lose_connection()
        await self.wait_until(lambda: run["id"] not in self.service.tasks)
        self.assertEqual(self.service.store.run(run["id"])["status"], "interrupted")
        self.assertTrue(browser.closed)

    async def test_browser_mode_is_a_task_snapshot_and_changes_only_apply_to_new_runs(self):
        self.service.browser_factory = DisconnectLearningBrowser
        first = self.service.start(self.course["id"])
        await self.wait_until(lambda: first["id"] in self.service.browsers)
        self.db.set_setting("preferences", {"learning": {"headless": False}})
        self.assertEqual(self.service.store.run(first["id"])["headless"], 1)
        self.assertTrue(self.service.browsers[first["id"]].headless)
        await self.service.control(first["id"], "stop")
        await self.wait_until(lambda: first["id"] not in self.service.tasks)
        second = self.service.start(self.course["id"])
        await self.wait_until(lambda: second["id"] in self.service.browsers)
        self.assertEqual(self.service.store.run(second["id"])["headless"], 0)
        self.assertFalse(self.service.browsers[second["id"]].headless)
        self.assertIn("open_browser", self.service.payload(self.service.store.run(second["id"]))["actions"])

    async def test_browser_tickets_are_one_use_and_bound_to_the_running_session(self):
        with self.assertRaisesRegex(RuntimeError, "浏览器"): self.service.browser_ticket("missing")
        self.service.browsers["run"] = object()
        try:
            token = self.service.browser_ticket("run")["token"]
            self.assertFalse(self.service.authorize_browser("other", token))
            token = self.service.browser_ticket("run")["token"]
            self.assertTrue(self.service.authorize_browser("run", token))
            self.assertFalse(self.service.authorize_browser("run", token))
        finally: self.service.browsers.pop("run")

    async def test_two_courses_of_one_account_run_and_stop_independently(self):
        records = self.service.store.upsert_courses(self.account["id"], [{"courseId": "second", "classId": "class", "courseName": "第二门测试网课", "url": "https://example.test/other"}], 2026, replace=False)
        other = next(item for item in records if item["id"] != self.course["id"])
        first, second = self.service.start(self.course["id"]), self.service.start(other["id"])
        await self.wait_until(lambda: all(identifier in self.service.browsers and self.service.browsers[identifier].played for identifier in (first["id"], second["id"])))
        first_browser, second_browser = self.service.browsers[first["id"]], self.service.browsers[second["id"]]
        self.assertIsNot(first_browser, second_browser)
        self.assertNotEqual(first_browser.account["_task_id"], second_browser.account["_task_id"])
        await self.service.control(first["id"], "pause")
        self.assertTrue(first_browser.paused)
        self.assertFalse(second_browser.paused)
        await self.service.control(first["id"], "stop")
        await self.wait_until(lambda: first["id"] not in self.service.tasks)
        self.assertTrue(first_browser.closed)
        self.assertFalse(second_browser.closed)
        self.assertEqual(self.service.store.run(second["id"])["status"], "running")

    async def test_browser_socket_requires_origin_and_ticket_and_closes_only_the_viewer(self):
        class Live:
            unsubscribed = False
            async def subscribe(self):
                self.queue = asyncio.Queue()
                self.queue.put_nowait({"type": "frame", "image": "frame"})
                return self.queue
            async def unsubscribe(self, queue): self.unsubscribed = True
            async def dispatch(self, value):
                BrowserInput.model_validate(value)
                if value.get("text") == "finish": self.queue.put_nowait({"type": "closed", "message": "closed"})
        from types import SimpleNamespace
        live = Live()
        self.service.browsers["run"] = SimpleNamespace(live=live)
        app = FastAPI()
        app.include_router(learning_router(self.service, lambda *args: None, lambda *args: None, lambda *args: None))
        try:
            with TestClient(app) as client:
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect("/api/learning/runs/run/browser", headers={"origin": "https://untrusted.test"}): pass
                with client.websocket_connect("/api/learning/runs/run/browser", headers={"origin": "http://127.0.0.1:5174"}) as ws:
                    ws.send_json({"token": "invalid"})
                    with self.assertRaises(WebSocketDisconnect): ws.receive_json()
                ticket = client.post("/api/learning/runs/run/browser/session").json()
                with client.websocket_connect("/api/learning/runs/run/browser", headers={"origin": "http://127.0.0.1:5174"}) as ws:
                    ws.send_json(ticket)
                    self.assertEqual(ws.receive_json()["type"], "frame")
                    ws.send_json({"type": "evaluate"})
                    with self.assertRaises(WebSocketDisconnect): ws.receive_json()
                ticket = client.post("/api/learning/runs/run/browser/session").json()
                with client.websocket_connect("/api/learning/runs/run/browser", headers={"origin": "http://127.0.0.1:5174"}) as ws:
                    ws.send_json(ticket); ws.receive_json()
                    ws.send_json({"type": "text", "text": "finish"})
                    self.assertEqual(ws.receive_json()["type"], "closed")
                    with self.assertRaises(WebSocketDisconnect): ws.receive_json()
            self.assertTrue(live.unsubscribed)
            self.assertIn("run", self.service.browsers)
        finally: self.service.browsers.pop("run")

    async def test_only_platform_verification_can_complete_and_empty_catalog_is_rejected(self):
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: run["id"] in self.service.browsers)
        browser = self.service.browsers[run["id"]]
        browser.finished = True
        await self.wait_until(lambda: run["id"] not in self.service.tasks)
        result = self.service.store.run(run["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["phase"], "verified")
        self.assertEqual(len(self.service.store.artifacts(run["id"])), 1)
        with self.assertRaises(ValueError): self.service.store.save_chapters(self.course["id"], [])

    async def test_unknown_task_keeps_browser_for_manual_continue(self):
        with patch.object(FakeLearningBrowser, "inspect", AsyncMock(return_value=({}, []))):
            run = self.service.start(self.course["id"])
            await self.wait_until(lambda: self.service.store.run(run["id"])["status"] == "awaiting_user", timeout=19)
            browser = self.service.browsers[run["id"]]
            self.assertFalse(browser.closed)
            browser.finished = True
            await self.service.control(run["id"], "continue")
            await self.wait_until(lambda: run["id"] not in self.service.tasks)
            self.assertEqual(self.service.store.run(run["id"])["status"], "completed")

    async def test_api_actions_errors_and_no_external_runtime_requirement(self):
        app = FastAPI()
        app.include_router(learning_router(self.service, lambda account: None, lambda account: None, lambda account: asyncio.Lock()))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            capabilities = (await client.get("/api/learning/capabilities")).json()
            self.assertFalse(capabilities["external_ocs_required"])
            response = await client.post("/api/learning/runs", json={"course_id": self.course["id"]})
            self.assertEqual(response.status_code, 200, response.text)
            identifier = response.json()["id"]
            duplicate = await client.post("/api/learning/runs", json={"course_id": self.course["id"]})
            self.assertEqual(duplicate.status_code, 409)
            stopped = await client.post(f"/api/learning/runs/{identifier}/stop")
            self.assertEqual(stopped.json()["status"], "stopping")
            self.assertEqual((await client.get("/api/learning/runs/missing")).status_code, 404)

    async def test_startup_recovery_releases_old_active_state(self):
        run = self.service.store.create_run(self.course["id"])
        self.service.store.recover()
        self.assertEqual(self.service.store.run(run["id"])["status"], "interrupted")
        self.assertIsNone(self.service.active(self.account["id"]))

    async def test_account_deletion_cascades_learning_history(self):
        run = self.service.store.create_run(self.course["id"])
        self.service.store.update(run["id"], status="stopped")
        self.service.store.event(run["id"], "stopped", "stopped")
        self.db.delete_account(self.account["id"])
        self.assertEqual(self.service.store.runs(self.account["id"]), [])

    async def test_learning_ownership_blocks_same_account_login_collect_and_preview_only(self):
        from app import main
        self.service.store.create_run(self.course["id"])
        other = self.db.create_account("other", "chaoxing", self.settings)
        with patch.object(main, "database", self.db), patch.object(main.preview_manager, "screenshot", AsyncMock(return_value={"image": "test"})) as screenshot:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                for method, path in [("POST", "login"), ("POST", "collect")]:
                    response = await client.request(method, f"/api/accounts/{self.account['id']}/{path}")
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn("网课", response.json()["detail"])
                response = await client.get(f"/api/accounts/{other['id']}/preview")
                self.assertEqual(response.status_code, 200, response.text)
                same = await client.get(f"/api/accounts/{self.account['id']}/preview")
                self.assertEqual(same.status_code, 200, same.text)
                self.assertEqual(screenshot.await_count, 2)


    async def test_course_skips_completed_chapters_and_rechecks_before_opening(self):
        class AlreadyFinishedBrowser(FakeLearningBrowser):
            def __init__(self, account, **kwargs):
                super().__init__(account, **kwargs)
                self.reads = 0
                self.open_chapter = AsyncMock()
            async def verify_catalog(self):
                self.reads += 1
                return [{"chapterId": "already", "title": "Download", "remaining": None, "completed": True},
                        {"chapterId": "changed", "title": "Video", "remaining": 0 if self.reads > 1 else 1, "completed": self.reads > 1}]
        self.service.browser_factory = AlreadyFinishedBrowser
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: run["id"] not in self.service.tasks)
        browser = AlreadyFinishedBrowser.instances[-1]
        browser.open_chapter.assert_not_awaited()
        self.assertFalse(browser.played)
        result = self.service.store.run(run["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["target"], ["changed"])
        self.assertEqual(result["completed"], ["changed"])
        self.assertTrue(any(event["kind"] == "chapter_skipped" for event in self.service.store.events(run["id"])))


class LearningRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_play_button_fallback_starts_real_media_without_resetting_progress(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(8000)
            output.writeframes(b"\0\0" * 80000)
        source = "data:audio/wav;base64," + base64.b64encode(buffer.getvalue()).decode()
        with tempfile.TemporaryDirectory() as root:
            browser = LearningBrowser({"profile_dir": str(Path(root) / "profile")}, authenticate=False)
            try:
                await browser.open()
                await browser.page.set_content(f'<audio src="{source}"></audio><button aria-label="播放" onclick="window.nativePlay()">Play</button>')
                await browser.page.wait_for_function("document.querySelector('audio').readyState >= 2")
                await browser.page.evaluate("""() => {
                    const media = document.querySelector('audio');
                    const nativePlay = media.play.bind(media);
                    media.currentTime = 2;
                    window.nativePlay = () => { window.clicks = (window.clicks || 0) + 1; return nativePlay(); };
                    media.play = () => Promise.reject(new DOMException('requires a play gesture', 'NotAllowedError'));
                }""")
                result = await browser.play(browser.page.main_frame)
                self.assertTrue(result['started'])
                self.assertEqual(result['method'], 'play_button')
                self.assertEqual(await browser.page.evaluate('window.clicks'), 1)
                self.assertTrue(await browser.page.locator('audio').evaluate('media => media.muted'))
                self.assertGreaterEqual(await browser.page.locator('audio').evaluate('media => media.currentTime'), 2)
                adopted = await browser.play(browser.page.main_frame)
                self.assertTrue(adopted['adopted'])
                self.assertEqual(await browser.page.evaluate('window.clicks'), 1)
                await browser.pause()
                await browser.page.evaluate("""() => {
                    const challenge = document.createElement('div');
                    challenge.id = 'captcha'; challenge.textContent = '请完成验证';
                    document.body.append(challenge);
                }""")
                blocked = await browser.play(browser.page.main_frame)
                self.assertFalse(blocked['started'])
                self.assertTrue(blocked['blocked'])
                self.assertEqual(await browser.page.evaluate('window.clicks'), 1)
            finally: await browser.close()

    async def test_modern_portal_catalog_matches_live_and_verification_html(self):
        html = '''<div class="chapter_item"><span>Group heading</span></div>
        <div class="chapter_item" title="XML" onclick="toOld('course', 'one', 'class',0)"><div class="catalog_task"><span class="catalog_points_yi">1</span></div></div>
        <div class="chapter_item" title="JDBC" onclick="toOld('course', 'two', 'class',0)"><div class="catalog_task"><span class="catalog_points_yi">0</span></div></div>
        <div class="chapter_item" title="Unknown" onclick="toOld('course', 'three', 'class',0)"></div>
        <div class="chapter_item" title="Download" onclick="toOld('course', 'four', 'class',0)"><div class="catalog_task"><div class="catalog_state icon_yiwanc"><span>已完成</span></div></div></div>'''
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                await page.set_content(html)
                await page.evaluate(RUNTIME)
                actual = await page.evaluate("window.__studioLearning.snapshot().chapters")
                fresh = await page.evaluate("html => window.__studioLearning.parseCatalog(html)", html)
                self.assertEqual(actual, fresh)
                self.assertEqual([item["chapterId"] for item in actual], ["one", "two", "three", "four"])
                self.assertEqual([item["completed"] for item in actual], [False, True, False, True])
                self.assertEqual([item["remaining"] for item in actual], [1, 0, None, None])
            finally: await browser.close()

    async def test_live_stream_and_mouse_keyboard_share_the_actual_browser_page(self):
        with tempfile.TemporaryDirectory() as profile:
            browser = LearningBrowser({"profile_dir": profile}, headless=True, authenticate=False)
            try:
                await browser.open()
                await browser.page.set_content('<input id="answer" style="position:absolute;left:100px;top:100px;width:200px;height:40px"><div style="height:3000px"></div>')
                first = await browser.live.subscribe()
                second = await browser.live.subscribe()
                frame = await asyncio.wait_for(first.get(), 5)
                self.assertEqual(frame["type"], "frame")
                self.assertTrue(base64.b64decode(frame["image"]).startswith(b"\xff\xd8"))
                await browser.live.dispatch({"type": "pointer", "action": "mousePressed", "button": "left", "buttons": 1, "x": 150/1440, "y": 120/900})
                await browser.live.dispatch({"type": "pointer", "action": "mouseReleased", "button": "left", "x": 150/1440, "y": 120/900})
                await browser.live.dispatch({"type": "text", "text": "studio live"})
                self.assertEqual(await browser.page.locator("#answer").input_value(), "studio live")
                await browser.page.locator("#answer").fill("native browser")
                await asyncio.wait_for(second.get(), 5)
                self.assertEqual(await browser.page.locator("#answer").input_value(), "native browser")
                await browser.live.unsubscribe(first)
                while not first.empty(): first.get_nowait()
                self.assertIsNotNone(browser.live.client)
                await browser.live.dispatch({"type": "wheel", "x": .8, "y": .8, "delta_y": 400})
                await browser.page.wait_for_function("window.scrollY > 0")
                self.assertEqual(first.qsize(), 0)
                with self.assertRaises(ValueError): BrowserInput.model_validate({"type": "evaluate", "text": "document.cookie"})
                with self.assertRaises(ValueError): BrowserInput.model_validate({"type": "pointer", "x": 5})
                await browser.live.unsubscribe(second)
                self.assertIsNone(browser.live.client)
                self.assertFalse(browser.page.is_closed())
            finally: await browser.close()

    async def test_real_audio_playback_pauses_and_resumes_without_reset(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(8000)
            output.writeframes(b"\0\0" * 24000)
        source = "data:audio/wav;base64," + base64.b64encode(buffer.getvalue()).decode()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                await page.set_content(f'<audio src="{source}"></audio>')
                await page.evaluate(RUNTIME)
                self.assertTrue((await page.evaluate("window.__studioLearning.play({playback_rate:1.5,volume:30,resource_wait_seconds:20})"))["started"])
                self.assertEqual(await page.locator("audio").evaluate("node => node.playbackRate"), 1.5)
                self.assertAlmostEqual(await page.locator("audio").evaluate("node => node.volume"), .3)
                await page.wait_for_timeout(300)
                await page.evaluate("window.__studioLearning.pause()")
                position = await page.locator("audio").evaluate("node => node.currentTime")
                await page.wait_for_timeout(1300)
                self.assertEqual(await page.locator("audio").evaluate("node => node.currentTime"), position)
                await page.evaluate("window.__studioLearning.play()")
                self.assertGreaterEqual(await page.locator("audio").evaluate("node => node.currentTime"), position)
                await page.wait_for_function("document.querySelector('audio').ended", timeout=6000)
            finally: await browser.close()

    async def test_reused_chapter_parser_waits_and_pause_do_not_restart_media(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                await page.set_content('''<div><a onclick="getTeacherAjax('c','class','one')">第一章</a><input class="jobUnfinishCount" value="1"></div><div><a onclick="getTeacherAjax('c','class','two')">第二章</a></div><audio></audio>''')
                await page.evaluate(RUNTIME)
                result = await page.evaluate("window.__studioLearning.snapshot()")
                self.assertEqual(result["chapters"][0]["remaining"], 1)
                self.assertIsNone(result["chapters"][1]["remaining"])
                self.assertFalse(result["chapters"][1]["completed"])
                paused = await page.evaluate("window.__studioLearning.pause()")
                self.assertTrue(paused["paused"])
                await page.wait_for_timeout(1200)
                self.assertTrue(await page.locator("audio").evaluate("node => node.paused"))
                self.assertFalse(await page.evaluate("typeof GM_getValue !== 'undefined'"))
                self.assertEqual(await page.evaluate("window.__studioLearning.parseCatalog('<div></div>')"), [])
            finally: await browser.close()


class LearningBrowserWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_browser_plays_fixture_then_verifies_fresh_platform_response(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(8000); output.writeframes(b"\0\0" * 8000)
        audio = base64.b64encode(buffer.getvalue()).decode()
        state = {"finished": False}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                if self.path == "/finished": state["finished"] = True
                self.send_response(200); self.end_headers()
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
                if self.path == "/media":
                    html = f'<audio controls src="data:audio/wav;base64,{audio}"></audio><script>document.querySelector("audio").addEventListener("ended",()=>fetch("/finished",{{method:"POST"}}));</script>'
                else:
                    remaining = 0 if state["finished"] else 1
                    html = f'''<div><a onclick="getTeacherAjax('c','class','chapter')">Test chapter</a><input class="jobUnfinishCount" value="{remaining}"></div><iframe src="/media" data='{{"jobid":"unit"}}'></iframe><script>window.getTeacherAjax=()=>{{}};window.attachments=[{{jobid:'unit',job:true,isPassed:false,property:{{mid:'unit',name:'Test audio'}}}}];</script>'''
                self.wfile.write(html.encode())
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        with tempfile.TemporaryDirectory() as root:
            settings = Settings(data_root=Path(root)); settings.ensure_directories()
            db = Database(settings); db.initialize()
            account = db.create_account("fixture", "chaoxing", settings)
            service = LearningService(db, settings, EventHub(), lambda account, **kwargs: LearningBrowser(account, headless=True, authenticate=False))
            service.initialize()
            course = service.store.upsert_courses(account["id"], [{"courseId": "c", "classId": "class", "courseName": "Fixture course", "url": f"http://127.0.0.1:{server.server_port}/course"}], 2026)[0]
            try:
                with patch("app.services.learning.service.send_completion_notification", AsyncMock(return_value={})):
                    run = service.start(course["id"])
                    await asyncio.wait_for(asyncio.shield(service.tasks[run["id"]]), 25)
                result = service.store.run(run["id"])
                self.assertTrue(state["finished"])
                self.assertEqual(result["status"], "completed", result["error"])
                self.assertTrue(service.store.artifacts(run["id"]))
                self.assertEqual(service.store.course(course["id"])["chapters"][0]["title"], "Test chapter")
                self.assertTrue(any(event["kind"] == "media_started" for event in service.store.events(run["id"])))
            finally:
                await service.shutdown()
                await asyncio.to_thread(server.shutdown); server.server_close(); thread.join(timeout=2)
