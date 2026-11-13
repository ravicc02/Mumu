from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.db import Database
from app.events import EventHub
from app.services.learning.browser import LearningBrowser
from app.services.learning.service import LearningService
from test_learning import FakeLearningBrowser


class ResourceBrowser(FakeLearningBrowser):
    chapter_ids = ["one"]
    broken_first = False
    confirm_reading = True

    def __init__(self, account, **kwargs):
        super().__init__(account, **kwargs)
        self.current, self.done, self.opened = "one", set(), []
        self.read_calls, self.play_calls = 0, 0

    async def open(self):
        self.learning_options.update(resource_wait_seconds=.02, confirmation_wait_seconds=.02)

    async def open_chapter(self, identifier):
        self.current = identifier
        self.opened.append(identifier)

    async def verify_catalog(self):
        return [{"chapterId": key, "title": key, "completed": key in self.done, "remaining": 0 if key in self.done else 1} for key in self.chapter_ids]

    async def inspect(self):
        key = self.current
        bad = self.broken_first and key == "one"
        unit = {"id": key, "title": key + (" document" if key == "one" else " video"), "mandatory": True, "completed": key in self.done}
        data = {"challenge": False, "quiz": False, "error": "fixture resource is broken" if bad else "",
            "read": key == "one", "resource": {"kind": "document" if key == "one" else "media", "ready": True},
            "media": None if key == "one" else {"paused": not self.played, "ended": False, "position": 0, "duration": 10}}
        return {key: unit}, [{"frame": self, "job_id": key, "data": data}]

    async def read_resource(self, frame):
        self.read_calls += 1
        if self.confirm_reading: self.done.add(self.current)
        return {"handled": True, "kind": "document"}

    async def play(self, frame):
        self.play_calls += 1
        self.played = True
        self.done.add(self.current)
        return {"started": True}


class ResourceQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.db = Database(self.settings); self.db.initialize()
        account = self.db.create_account("fixture", "chaoxing", self.settings)
        self.db.update_account(account["id"], status="ready")
        self.course = self._course(account["id"])
        self.service = LearningService(self.db, self.settings, EventHub(), ResourceBrowser)
        self.service.initialize()
        self.notifications = patch("app.services.learning.service.send_completion_notification", AsyncMock(return_value={}))
        self.notifications.start()

    def _course(self, account):
        from app.services.learning.store import LearningStore
        store = LearningStore(self.db); store.initialize()
        return store.upsert_courses(account, [{"courseId": "course", "classId": "class", "courseName": "fixture", "url": "https://example.test/course"}], 2026)[0]

    async def asyncTearDown(self):
        await self.service.shutdown()
        self.notifications.stop()
        self.temp.cleanup()

    async def wait_for(self, predicate, timeout=10):
        async with asyncio.timeout(timeout):
            while not predicate(): await asyncio.sleep(.02)

    async def test_document_reader_runs_and_only_platform_confirmation_completes(self):
        run = self.service.start(self.course["id"])
        await self.wait_for(lambda: self.service.store.run(run["id"])["status"] == "completed")
        events = self.service.store.events(run["id"])
        self.assertTrue(any(item["kind"] == "reading" for item in events))
        self.assertTrue(any(item["kind"] == "resource_verifying" for item in events))
        self.assertEqual(self.service.store.run(run["id"])["completed"], ["one"])

    async def test_broken_resource_does_not_block_later_chapter_and_is_not_counted_done(self):
        class MixedBrowser(ResourceBrowser):
            chapter_ids = ["one", "two"]
            broken_first = True
        self.service.browser_factory = MixedBrowser
        run = self.service.start(self.course["id"])
        await self.wait_for(lambda: self.service.store.run(run["id"])["status"] == "awaiting_user")
        browser = self.service.browsers[run["id"]]
        self.assertEqual(browser.play_calls, 1)
        self.assertIn("two", browser.opened)
        current = self.service.store.run(run["id"])
        self.assertEqual(current["completed"], ["two"])
        self.assertEqual(set(current["target"]), {"one", "two"})
        self.assertFalse(browser.closed)
        self.assertEqual(sum(item["kind"] == "awaiting_user" for item in self.service.store.events(run["id"])), 1)
        browser.done.add("one")
        await self.service.control(run["id"], "continue")
        await self.wait_for(lambda: self.service.store.run(run["id"])["status"] == "completed")

    async def test_handled_read_without_platform_receipt_is_deferred_once_then_other_work_runs(self):
        class UnconfirmedBrowser(ResourceBrowser):
            chapter_ids = ["one", "two"]
            confirm_reading = False
        self.service.browser_factory = UnconfirmedBrowser
        run = self.service.start(self.course["id"])
        await self.wait_for(lambda: self.service.store.run(run["id"])["status"] == "awaiting_user")
        browser = self.service.browsers[run["id"]]
        self.assertEqual(browser.read_calls, 1)
        self.assertEqual(browser.play_calls, 1)
        self.assertEqual(self.service.store.run(run["id"])["completed"], ["two"])

    async def test_chapter_load_error_is_recorded_and_next_chapter_runs(self):
        class LoadFailure(ResourceBrowser):
            chapter_ids = ["one", "two"]
            async def open_chapter(self, identifier):
                if identifier == "one": raise RuntimeError("fixture chapter load timeout")
                await super().open_chapter(identifier)
        self.service.browser_factory = LoadFailure
        run = self.service.start(self.course["id"])
        await self.wait_for(lambda: self.service.store.run(run["id"])["status"] == "awaiting_user")
        self.assertEqual(self.service.browsers[run["id"]].play_calls, 1)
        self.assertEqual(self.service.store.run(run["id"])["completed"], ["two"])

    async def test_transient_catalog_errors_retry_but_authentication_errors_do_not(self):
        from app.services.agent_runner import JobControl
        run = self.service.store.create_run(self.course["id"])
        browser = ResourceBrowser({})
        browser.verify_catalog = AsyncMock(side_effect=[RuntimeError("HTTP 503"), TimeoutError("timed out"), [{"chapterId": "one", "title": "one", "completed": True}]])
        result = await self.service.verify_catalog(run["id"], browser, JobControl())
        self.assertTrue(result[0]["completed"])
        self.assertEqual(browser.verify_catalog.await_count, 3)
        browser.verify_catalog = AsyncMock(side_effect=RuntimeError("平台登录状态已失效"))
        with self.assertRaisesRegex(RuntimeError, "登录"):
            await self.service.verify_catalog(run["id"], browser, JobControl())
        self.assertEqual(browser.verify_catalog.await_count, 1)

    async def test_bad_card_does_not_block_video_on_the_next_card_of_the_same_chapter(self):
        class CardBrowser(ResourceBrowser):
            def __init__(self, account, **kwargs):
                super().__init__(account, **kwargs)
                self.card = 0
            async def inspect(self):
                key = "bad" if self.card == 0 else "good"
                data = {"challenge": False, "quiz": False, "error": "fixture video load failure" if self.card == 0 else "",
                    "media": {"paused": not self.played, "ended": False, "position": 0, "duration": 10}}
                return {key: {"id": key, "title": key, "mandatory": True, "completed": self.played and self.card == 1}}, [{"job_id": key, "frame": self, "data": data}]
            async def play(self, frame):
                self.play_calls += 1; self.played = True
                return {"started": True}
            async def advance_resource_card(self):
                if self.card: return False
                self.card = 1; return True
        self.service.browser_factory = CardBrowser
        run = self.service.start(self.course["id"])
        await self.wait_for(lambda: self.service.store.run(run["id"])["status"] == "awaiting_user")
        browser = self.service.browsers[run["id"]]
        self.assertEqual(browser.play_calls, 1)
        self.assertEqual(self.service.store.run(run["id"])["completed"], [])
        self.assertTrue(any(item["kind"] == "resource_tab" for item in self.service.store.events(run["id"])))

    async def test_identity_verification_stops_actions_instead_of_skipping_authentication(self):
        class ChallengeBrowser(ResourceBrowser):
            async def inspect(self):
                attachments, frames = await super().inspect()
                frames[0]["data"]["challenge"] = True
                return attachments, frames
        self.service.browser_factory = ChallengeBrowser
        run = self.service.start(self.course["id"])
        await self.wait_for(lambda: self.service.store.run(run["id"])["status"] == "awaiting_user")
        browser = self.service.browsers[run["id"]]
        self.assertEqual(browser.read_calls, 0)
        self.assertIn("身份验证", self.service.store.run(run["id"])["error"])


class ReaderRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.browser = LearningBrowser({"profile_dir": str(Path(self.temp.name) / "profile")}, authenticate=False)
        await self.browser.open()

    async def asyncTearDown(self):
        await self.browser.close()
        self.temp.cleanup()

    async def test_real_document_calls_original_upstream_handler(self):
        await self.browser.page.set_content('<div id="img" class="imglook"></div><script>window.finishJob=()=>{window.finished=(window.finished||0)+1}</script>')
        result = await self.browser.read_resource(self.browser.page.main_frame)
        self.assertTrue(result["handled"])
        self.assertEqual(await self.browser.page.evaluate("window.finished"), 1)

    async def test_audio_ppt_advances_all_slides_through_upstream_handler(self):
        await self.browser.page.set_content('<div class="swiper-container"><div class="swiper-slide"></div><div class="swiper-slide"></div></div><script>window.slides=0;window.swiperNext=()=>window.slides++</script>')
        result = await self.browser.read_resource(self.browser.page.main_frame)
        self.assertEqual(result["kind"], "audio_ppt")
        self.assertEqual(await self.browser.page.evaluate("window.slides"), 2)

    async def test_hyperlink_uses_upstream_click_and_restores_inline_handler(self):
        await self.browser.page.set_content('<a id="hyperlink" href="#" onclick="window.inline=true">Read</a><script>window.clicked=0;document.querySelector("a").addEventListener("click",()=>window.clicked++)</script>')
        result = await self.browser.read_resource(self.browser.page.main_frame)
        self.assertTrue(result["handled"])
        self.assertEqual(await self.browser.page.evaluate("window.clicked"), 1)
        self.assertTrue(await self.browser.page.evaluate("typeof document.querySelector('a').onclick==='function'"))

    async def test_reading_wait_is_cancelled_by_manual_pause(self):
        await self.browser.page.set_content('<div id="img" class="imglook"></div><script>window.finishJob=()=>{}</script>')
        reading = asyncio.create_task(self.browser.read_resource(self.browser.page.main_frame))
        await asyncio.sleep(.2)
        await self.browser.pause()
        with self.assertRaisesRegex(Exception, "取消"): await reading

    async def test_resource_tabs_are_visited_once_and_have_a_real_end(self):
        await self.browser.page.set_content('<div id="prev_tab"><ul class="prev_ul"><li class="active">one</li><li>two</li><li>three</li></ul></div><script>document.querySelectorAll("li").forEach(el=>el.onclick=()=>{document.querySelectorAll("li").forEach(x=>x.className="");el.className="active"})</script>')
        self.assertTrue(await self.browser.advance_resource_card())
        self.assertTrue(await self.browser.advance_resource_card())
        self.assertFalse(await self.browser.advance_resource_card())
        self.assertEqual(len(self.browser.visited_resource_tabs), 2)

    async def test_timed_reader_waits_for_real_intervals_and_pause_keeps_phase(self):
        html = '<div id="reader"></div><select id="pagejump"><option value="1">start</option><option value="5">body</option><option value="7">end</option></select><button class="readerPager" style="z-index:101" onclick="window.finished=true">Finish</button>'
        await self.browser.page.route('**/readsvr/book/mooc*', lambda route: route.fulfill(content_type="text/html", body=html))
        await self.browser.page.goto('http://fixture.test/readsvr/book/mooc?timing=0')
        first = await self.browser.read_resource(self.browser.page.main_frame)
        self.assertTrue(first["waiting"])
        await asyncio.sleep(3.05)
        middle = await self.browser.read_resource(self.browser.page.main_frame)
        self.assertEqual(middle["phase"], 1)
        await self.browser.pause()
        resumed = await self.browser.read_resource(self.browser.page.main_frame)
        self.assertEqual(resumed["phase"], 1)
        await asyncio.sleep(3.05)
        end = await self.browser.read_resource(self.browser.page.main_frame)
        self.assertEqual(end["phase"], 2)
        await asyncio.sleep(3.05)
        self.assertTrue((await self.browser.read_resource(self.browser.page.main_frame))["handled"])
        self.assertTrue(await self.browser.page.evaluate('window.finished'))
