from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from playwright.async_api import async_playwright

from app import main
from app.config import Settings
from app.db import Database
from app.events import EventHub
from app.services.agent_runner import _launch_chrome, _terminate_process
from app.services.assignment_live import AssignmentLiveBrowser
from app.services.browser import TaskBrowserProfile
from app.services.learning.browser import LearningBrowser
from app.services.learning.service import LearningService
from app.services.platform_auth import restore_state
from app.services.preview import ProfilePreviewManager


class BrowserProfileTests(unittest.TestCase):
    def test_task_directories_are_distinct_and_cleanup_preserves_authorization(self):
        with tempfile.TemporaryDirectory() as folder:
            canonical = Path(folder) / "profiles" / "account"
            canonical.mkdir(parents=True)
            original = canonical / "keep.txt"; original.write_text("account profile")
            account = {"profile_dir": str(canonical)}
            first = TaskBrowserProfile(account, "assignment", "one")
            second = TaskBrowserProfile(account, "assignment", "two")
            course = TaskBrowserProfile(account, "learning", "one")
            self.assertEqual(len({first.path, second.path, course.path, canonical}), 4)
            first.close()
            self.assertFalse(first.path.exists())
            self.assertTrue(second.path.exists())
            self.assertTrue(course.path.exists())
            self.assertEqual(original.read_text(), "account profile")
            second.close(); course.close(); first.close()

    def test_cleanup_rejects_a_directory_outside_the_owned_root(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = TaskBrowserProfile({"profile_dir": str(Path(folder) / "profiles" / "account")}, "assignment", "../unsafe")
            owned = profile.path
            profile.path = Path(folder)
            with self.assertRaises(RuntimeError): profile.close()
            self.assertTrue(Path(folder).exists())
            profile.path = owned; profile.close()


class TaskBrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_assignment_windows_reuse_only_the_same_task_and_cleanup_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(data_root=Path(folder)); settings.ensure_directories()
            database = Database(settings); database.initialize()
            account = database.create_account("fixture", "chaoxing", settings)
            pages = [SimpleNamespace(url="https://example.test/" + suffix, is_closed=lambda: False,
                goto=AsyncMock(), bring_to_front=AsyncMock(), on=Mock()) for suffix in ("one", "two")]
            contexts = [SimpleNamespace(pages=[page], close=AsyncMock(), on=Mock()) for page in pages]
            launch = AsyncMock(side_effect=contexts)
            manager = ProfilePreviewManager()
            manager._playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch), stop=AsyncMock())
            try:
                with patch("app.services.preview.restore_state", AsyncMock()):
                    await manager.open_interactive(database, account["id"], target_url=pages[0].url, task_id="one")
                    await manager.open_interactive(database, account["id"], target_url=pages[1].url, task_id="two")
                    await manager.open_interactive(database, account["id"], target_url=pages[0].url, task_id="one")
                self.assertEqual(launch.await_count, 2)
                first, second = [Path(call.args[0]) for call in launch.call_args_list]
                self.assertNotEqual(first, second)
                self.assertNotEqual(first, Path(account["profile_dir"]))
                contexts[1].close.assert_not_awaited()
                await manager.invalidate(account["id"])
                for context in contexts: context.close.assert_awaited_once()
                self.assertFalse(first.exists()); self.assertFalse(second.exists())
                self.assertFalse(manager._contexts)
            finally: await manager.close()

    async def test_same_account_assignment_and_learning_have_independent_processes_live_input_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "profiles" / "account"; profile.mkdir(parents=True)
            state = Path(folder) / "state.json"
            state.write_text(json.dumps({"cookies": [{"name": "fixture_session", "value": "same-account", "domain": "127.0.0.1", "path": "/", "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Lax"}],
                "origins": [{"origin": "http://127.0.0.1:19346", "localStorage": [{"name": "fixture_auth", "value": "shared-authorization"}]}]}))
            account = {"id": "fixture", "profile_dir": str(profile), "storage_state_path": str(state)}
            assignment = TaskBrowserProfile(account, "assignment", "work")
            course = LearningBrowser({**account, "_task_id": "course"}, headless=True, authenticate=False)
            process = viewer = None
            try:
                await course.open()
                await restore_state(course.context, str(state))
                process, endpoint = await _launch_chrome(str(assignment.path), headless=True, timeout=20)
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.connect_over_cdp(endpoint)
                    context = browser.contexts[0]
                    await restore_state(context, str(state))
                    await context.route("**/*", lambda route: route.fulfill(content_type="text/html", body='<title>Assignment</title><input id="answer" style="position:absolute;left:100px;top:100px;width:220px;height:45px"><div style="height:3000px"></div>'))
                    await course.context.route("**/*", lambda route: route.fulfill(content_type="text/html", body='<title>Course</title><input id="answer" style="position:absolute;left:100px;top:100px;width:220px;height:45px"><div style="height:3000px"></div>'))
                    page = context.pages[0]
                    await page.goto("http://127.0.0.1:19346/work")
                    await course.page.goto("http://127.0.0.1:19346/studentstudy")
                    for active in (page, course.page):
                        self.assertIn("fixture_session=same-account", await active.evaluate("document.cookie"))
                        self.assertEqual(await active.evaluate("localStorage.getItem('fixture_auth')"), "shared-authorization")
                    await page.evaluate("localStorage.setItem('isolated','assignment')")
                    self.assertIsNone(await course.page.evaluate("localStorage.getItem('isolated')"))
                    client = await context.new_cdp_session(page)
                    target = (await client.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
                    viewer = AssignmentLiveBrowser(SimpleNamespace(cdp_url=endpoint, get_focused_target=lambda: SimpleNamespace(target_id=target, url=page.url)))
                    first, second = await asyncio.gather(viewer.live.subscribe(), course.live.subscribe())
                    frames = await asyncio.gather(asyncio.wait_for(first.get(), 8), asyncio.wait_for(second.get(), 8))
                    self.assertTrue(all(frame["type"] == "frame" for frame in frames))
                    await page.locator("#answer").focus()
                    await viewer.live.dispatch({"type": "text", "text": "assignment only"})
                    self.assertEqual(await page.locator("#answer").input_value(), "assignment only")
                    self.assertEqual(await course.page.locator("#answer").input_value(), "")
                    await course.page.locator("#answer").focus()
                    await course.live.dispatch({"type": "text", "text": "course only"})
                    self.assertEqual(await course.page.locator("#answer").input_value(), "course only")
                    self.assertEqual(await page.locator("#answer").input_value(), "assignment only")
                    await viewer.live.close(); viewer = None
                    await browser.close(); await _terminate_process(process); process = None
                    assignment.close()
                    self.assertFalse(assignment.path.exists())
                    self.assertTrue(course.workspace.path.exists())
                    self.assertFalse(course.page.is_closed())
                    await course.page.locator("#answer").fill("still running")
                    self.assertEqual(await course.page.locator("#answer").input_value(), "still running")
                    await course.live.dispatch({"type": "wheel", "x": .8, "y": .8, "delta_y": 400})
                    await course.page.wait_for_function("scrollY > 100")
                    await course.live.unsubscribe(second)
            finally:
                if viewer: await viewer.live.close()
                await _terminate_process(process)
                assignment.close()
                await course.close()
            self.assertFalse(course.workspace.path.exists())
            self.assertTrue(state.is_file())

    async def test_api_allows_assignment_with_a_course_and_preserves_duplicate_assignment_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(data_root=Path(folder), openai_api_key="fixture")
            settings.ensure_directories()
            database = Database(settings); database.initialize()
            account = database.create_account("fixture", "chaoxing", settings)
            database.update_account(account["id"], status="ready")
            learning = LearningService(database, settings, EventHub()); learning.initialize()
            courses = learning.store.upsert_courses(account["id"], [{"courseId": "course", "classId": "class", "courseName": "fixture", "url": "http://example.test/course"}], 2026)
            learning.store.create_run(courses[0]["id"])
            assignment = database.upsert_assignment(account["id"], "chaoxing", {"academicYear": 2026, "courseName": "fixture", "courseId": "course", "classId": "class", "workId": "work", "title": "fixture", "status": "unfinished", "submittable": True, "taskUrl": "http://example.test/work"}, settings)
            with patch.object(main, "database", database), patch.object(main, "learning_service", learning), patch.object(main, "effective_settings", return_value=settings), patch.object(main, "start_job") as start:
                created = await main.create_job(main.CreateJobRequest(assignment_id=assignment))
                self.assertEqual(created["status"], "queued")
                self.assertEqual(created["browser_task_id"], created["id"])
                start.assert_called_once()
                main.ensure_learning_start(account["id"])
                from fastapi import HTTPException
                with self.assertRaises(HTTPException): await main.create_job(main.CreateJobRequest(assignment_id=assignment))
