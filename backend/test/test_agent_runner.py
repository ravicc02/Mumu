from __future__ import annotations

import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.db import Database
from app.events import EventHub
from app.services import agent_runner


class FakeProcess:
    pid = 4242
    returncode = None


class FakeCurrentState:
    next_goal = "填写第一题"


class FakeOutput:
    current_state = FakeCurrentState()
    action = []


class FakeHistory:
    def final_result(self) -> str:
        return "所有题目已填写"

    def is_successful(self) -> bool:
        return True

    def model_dump(self, mode: str = "json") -> dict:
        return {"mode": mode, "steps": 1}


class FakeAgent:
    def __init__(self, **kwargs):
        self.on_step = kwargs["register_new_step_callback"]

    async def run(self, max_steps: int, on_step_start) -> FakeHistory:
        await on_step_start(self)
        await self.on_step(None, FakeOutput(), 0)
        return FakeHistory()


class FakeIncompleteHistory(FakeHistory):
    def final_result(self) -> str:
        return "仍有题目未完成"

    def is_successful(self) -> bool:
        return False


class FakeIncompleteAgent(FakeAgent):
    async def run(self, max_steps: int, on_step_start) -> FakeIncompleteHistory:
        await on_step_start(self)
        await self.on_step(None, FakeOutput(), 0)
        return FakeIncompleteHistory()


class FakeBrowserSession:
    def __init__(self, **_kwargs):
        pass

    async def start(self) -> None:
        return None

    async def kill(self) -> None:
        return None

    async def get_current_page_url(self) -> str:
        return "https://example.test/assignment"


async def fake_restore_storage_state(*_args, **_kwargs) -> int:
    return 1


async def fake_verify_session_page(_session, url: str, **_kwargs) -> str:
    return url


class FakeFocusPage:
    session_id = "page-session"


class FakeFocusSession:
    def __init__(self) -> None:
        self.called = False
        session = self

        class PageApi:
            async def bringToFront(self, **kwargs):
                session.called = kwargs.get("session_id") == "page-session"

        class SendApi:
            Page = PageApi()

        class CdpClient:
            send = SendApi()

        self.cdp_client = CdpClient()

    async def get_current_page(self):
        return FakeFocusPage()


class AgentRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_object_client_wrapper_injects_response_format(self) -> None:
        captured = {}

        class Delegate:
            async def create(self, **kwargs):
                captured.update(kwargs)
                return "ok"

        completions = agent_runner._JsonObjectCompletions(Delegate())
        result = await completions.create(model="test-model", messages=[])
        self.assertEqual(result, "ok")
        self.assertEqual(captured["response_format"], {"type": "json_object"})
        self.assertIs(
            agent_runner.browser_use_utils.SignalHandler,
            agent_runner._NoopSignalHandler,
        )
        self.assertEqual(
            agent_runner._clean_json_content('```json\n{"ok": true}\n```'),
            '{"ok": true}',
        )

    async def test_pause_resume_and_stop_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(data_root=Path(temp_dir))
            settings.ensure_directories()
            database = Database(settings)
            database.initialize()
            account = database.create_account("测试用户", "chaoxing", settings)
            assignment_id = database.upsert_assignment(
                account["id"],
                "chaoxing",
                {
                    "academicYear": 2026,
                    "courseName": "软件工程",
                    "courseId": "course-1",
                    "classId": "class-1",
                    "title": "实验一",
                    "status": "unfinished",
                    "submittable": True,
                    "taskUrl": "https://example.test/assignment",
                    "workId": "work-1",
                },
                settings,
            )
            job = database.create_job(
                account["id"],
                assignment_id,
                auto_submit=False,
            )
            database.transition_job(job["id"], "running")
            hub = EventHub()
            control = agent_runner.JobControl()
            agent_runner._job_controls[job["id"]] = control
            try:
                agent_runner.pause_job(database, hub, job["id"])
                self.assertEqual(database.get_job(job["id"])["status"], "paused")
                with self.assertRaises(RuntimeError):
                    agent_runner.pause_job(database, hub, job["id"])

                agent_runner.resume_job(database, hub, job["id"])
                self.assertEqual(database.get_job(job["id"])["status"], "running")

                agent_runner.stop_job(database, hub, job["id"])
                self.assertEqual(database.get_job(job["id"])["status"], "stopping")
                with self.assertRaises(RuntimeError):
                    agent_runner.stop_job(database, hub, job["id"])
            finally:
                agent_runner._job_controls.pop(job["id"], None)

    async def test_resuming_paused_queued_job_returns_to_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(data_root=Path(temp_dir))
            settings.ensure_directories()
            database = Database(settings)
            database.initialize()
            account = database.create_account("测试用户", "chaoxing", settings)
            assignment_id = database.upsert_assignment(
                account["id"],
                "chaoxing",
                {
                    "academicYear": 2026,
                    "courseName": "软件工程",
                    "courseId": "course-1",
                    "classId": "class-1",
                    "title": "实验一",
                    "status": "unfinished",
                    "submittable": True,
                    "taskUrl": "https://example.test/assignment",
                    "workId": "work-1",
                },
                settings,
            )
            job = database.create_job(
                account["id"],
                assignment_id,
                auto_submit=False,
            )
            hub = EventHub()
            agent_runner._job_controls[job["id"]] = agent_runner.JobControl()
            try:
                agent_runner.pause_job(database, hub, job["id"])
                self.assertEqual(
                    database.get_job(job["id"])["paused_from_status"],
                    "queued",
                )
                agent_runner.resume_job(database, hub, job["id"])
                resumed = database.get_job(job["id"])
                self.assertEqual(resumed["status"], "queued")
                self.assertIsNone(resumed["paused_from_status"])
            finally:
                agent_runner._job_controls.pop(job["id"], None)

    async def test_force_stop_cancels_hung_job(self) -> None:
        async def hung_job() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(hung_job())
        agent_runner._running_jobs["hung-job"] = task
        try:
            await agent_runner._force_stop_after("hung-job", timeout=0.01)
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            agent_runner._running_jobs.pop("hung-job", None)

    async def test_focus_job_browser_brings_page_to_front(self) -> None:
        session = FakeFocusSession()
        agent_runner._job_sessions["focus-job"] = session
        try:
            self.assertTrue(await agent_runner.focus_job_browser("focus-job"))
            self.assertTrue(session.called)
        finally:
            agent_runner._job_sessions.pop("focus-job", None)

    async def test_restore_storage_state_injects_cookies_and_origin_script(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.cookies = []
                self.scripts = []

            async def get_current_page(self):
                return object()

            async def _cdp_set_cookies(self, cookies):
                self.cookies = cookies

            async def _cdp_add_init_script(self, script):
                self.scripts.append(script)
                return "script-id"

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "storage-state.json"
            state_path.write_text(
                """
                {
                  "cookies": [
                    {
                      "name": "sid",
                      "value": "secret",
                      "domain": ".chaoxing.com",
                      "path": "/",
                      "expires": -1,
                      "httpOnly": true,
                      "secure": true,
                      "sameSite": "Lax"
                    }
                  ],
                  "origins": [
                    {
                      "origin": "https://i.chaoxing.com",
                      "localStorage": [{"name": "k", "value": "v"}],
                      "sessionStorage": []
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )
            session = Session()
            count = await agent_runner._restore_storage_state_to_session(
                session,
                str(state_path),
            )
            self.assertEqual(count, 1)
            self.assertEqual(session.cookies[0]["name"], "sid")
            self.assertNotIn("expires", session.cookies[0])
            self.assertIn("localStorage.setItem", session.scripts[0])

    async def test_verify_session_page_rejects_passport_redirect(self) -> None:
        class Page:
            session_id = "page-session"

        class Navigate:
            async def __call__(self, **kwargs):
                return {"frameId": "frame"}

        class PageApi:
            navigate = Navigate()

        class SendApi:
            Page = PageApi()

        class CdpClient:
            send = SendApi()

        class Session:
            cdp_client = CdpClient()

            async def get_current_page(self):
                return Page()

            async def get_current_page_url(self):
                return "https://passport2.chaoxing.com/login"

        with self.assertRaisesRegex(RuntimeError, "未继承账号登录态"):
            await agent_runner._verify_session_page(
                Session(),
                "https://i.chaoxing.com/base?ws=1",
                timeout=1,
            )

    async def test_job_reaches_completed_with_logs_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                data_root=Path(temp_dir),
                openai_api_key="test-key",
                openai_base_url="https://example.test/v1",
                openai_model="test-model",
            )
            settings.ensure_directories()
            database = Database(settings)
            database.initialize()
            account = database.create_account(
                "测试用户",
                "chaoxing",
                settings,
            )
            assignment_id = database.upsert_assignment(
                account["id"],
                "chaoxing",
                {
                    "academicYear": 2026,
                    "semester": "2026-2027-1",
                    "courseName": "软件工程",
                    "courseId": "course-1",
                    "classId": "class-1",
                    "title": "实验一",
                    "status": "unfinished",
                    "submittable": True,
                    "taskUrl": "https://example.test/assignment",
                    "workId": "work-1",
                },
                settings,
            )
            job = database.create_job(
                account["id"],
                assignment_id,
                auto_submit=False,
                interactive=True,
            )

            async def fake_launch(*_args, **_kwargs):
                return FakeProcess(), "ws://127.0.0.1:9999/devtools/browser/test"

            async def fake_capture(*_args, **_kwargs):
                return b"png-data", "https://example.test/assignment"

            async def fake_invalidate(_account_id: str) -> None:
                return None

            async def fake_notify(*_args, **_kwargs) -> None:
                return {"weixin": True}

            hub = EventHub()
            with (
                patch.object(agent_runner, "_launch_chrome", fake_launch),
                patch.object(
                    agent_runner,
                    "_capture_screenshot",
                    fake_capture,
                ),
                patch.object(agent_runner, "BrowserSession", FakeBrowserSession),
                patch.object(
                    agent_runner,
                    "_restore_storage_state_to_session",
                    fake_restore_storage_state,
                ),
                patch.object(
                    agent_runner,
                    "_verify_session_page",
                    fake_verify_session_page,
                ),
                patch.object(agent_runner, "ChatOpenAI", lambda **_kwargs: object()),
                patch.object(agent_runner, "Agent", FakeAgent),
                patch.object(
                    agent_runner.preview_manager,
                    "invalidate",
                    fake_invalidate,
                ),
                patch.object(
                    agent_runner,
                    "send_completion_notification",
                    fake_notify,
                ),
                patch.object(
                    agent_runner,
                    "_terminate_process",
                    fake_notify,
                ),
            ):
                await agent_runner.run_job(
                    database,
                    hub,
                    settings,
                    job["id"],
                )

            completed = database.get_job(job["id"])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["progress"], 100)
            event_kinds = {
                event["kind"]
                for event in database.list_events(job["id"])
            }
            self.assertTrue(
                {
                    "queued",
                    "status",
                    "browser_starting",
                    "browser_ready",
                    "browser",
                    "thinking",
                    "step",
                    "screenshot",
                    "final_screenshot",
                    "completed",
                }.issubset(event_kinds)
            )
            artifact_kinds = {
                artifact["kind"]
                for artifact in database.list_artifacts(job["id"])
            }
            self.assertEqual(
                artifact_kinds,
                {"screenshot", "final_screenshot", "result_json"},
            )

    async def test_agent_reported_incomplete_is_not_marked_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                data_root=Path(temp_dir),
                openai_api_key="test-key",
                openai_base_url="https://example.test/v1",
                openai_model="test-model",
            )
            settings.ensure_directories()
            database = Database(settings)
            database.initialize()
            account = database.create_account("测试用户", "chaoxing", settings)
            assignment_id = database.upsert_assignment(
                account["id"],
                "chaoxing",
                {
                    "academicYear": 2026,
                    "courseName": "软件工程",
                    "courseId": "course-1",
                    "classId": "class-1",
                    "title": "实验一",
                    "status": "unfinished",
                    "submittable": True,
                    "taskUrl": "https://example.test/assignment",
                    "workId": "work-1",
                },
                settings,
            )
            job = database.create_job(
                account["id"],
                assignment_id,
                auto_submit=False,
                interactive=False,
            )

            async def fake_launch(*_args, **_kwargs):
                return FakeProcess(), "ws://127.0.0.1:9999/devtools/browser/test"

            async def fake_capture(*_args, **_kwargs):
                return b"png-data", "https://example.test/assignment"

            async def no_op(*_args, **_kwargs) -> None:
                return None

            async def fake_notify(*_args, **_kwargs) -> dict:
                return {"weixin": False}

            with (
                patch.object(agent_runner, "_launch_chrome", fake_launch),
                patch.object(agent_runner, "_capture_screenshot", fake_capture),
                patch.object(agent_runner, "BrowserSession", FakeBrowserSession),
                patch.object(
                    agent_runner,
                    "_restore_storage_state_to_session",
                    fake_restore_storage_state,
                ),
                patch.object(
                    agent_runner,
                    "_verify_session_page",
                    fake_verify_session_page,
                ),
                patch.object(agent_runner, "ChatOpenAI", lambda **_kwargs: object()),
                patch.object(agent_runner, "JsonObjectChatOpenAI", lambda **_kwargs: object()),
                patch.object(agent_runner, "Agent", FakeIncompleteAgent),
                patch.object(
                    agent_runner.preview_manager,
                    "invalidate",
                    no_op,
                ),
                patch.object(agent_runner, "_terminate_process", no_op),
                patch.object(
                    agent_runner,
                    "send_completion_notification",
                    fake_notify,
                ),
            ):
                await agent_runner.run_job(
                    database,
                    EventHub(),
                    settings,
                    job["id"],
                )

            result = database.get_job(job["id"])
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(
                result["submission_status"],
                "partial_pending_review",
            )
            self.assertIn(
                "incomplete",
                {
                    event["kind"]
                    for event in database.list_events(job["id"])
                },
            )

    async def test_notification_failure_does_not_change_completed_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                data_root=Path(temp_dir),
                openai_api_key="test-key",
                openai_base_url="https://example.test/v1",
                openai_model="test-model",
            )
            settings.ensure_directories()
            database = Database(settings)
            database.initialize()
            account = database.create_account("测试用户", "chaoxing", settings)
            assignment_id = database.upsert_assignment(
                account["id"],
                "chaoxing",
                {
                    "academicYear": 2026,
                    "courseName": "软件工程",
                    "courseId": "course-1",
                    "classId": "class-1",
                    "title": "实验一",
                    "status": "unfinished",
                    "submittable": True,
                    "taskUrl": "https://example.test/assignment",
                    "workId": "work-1",
                },
                settings,
            )
            job = database.create_job(
                account["id"],
                assignment_id,
                auto_submit=False,
                interactive=True,
            )

            async def fake_launch(*_args, **_kwargs):
                return FakeProcess(), "ws://127.0.0.1:9999/devtools/browser/test"

            async def fake_capture(*_args, **_kwargs):
                return b"png-data", "https://example.test/assignment"

            async def fake_invalidate(_account_id: str) -> None:
                return None

            async def no_op(*_args, **_kwargs) -> None:
                return None

            async def failing_notify(*_args, **_kwargs) -> None:
                raise RuntimeError("notification unavailable")

            with (
                patch.object(agent_runner, "_launch_chrome", fake_launch),
                patch.object(agent_runner, "_capture_screenshot", fake_capture),
                patch.object(agent_runner, "BrowserSession", FakeBrowserSession),
                patch.object(
                    agent_runner,
                    "_restore_storage_state_to_session",
                    fake_restore_storage_state,
                ),
                patch.object(
                    agent_runner,
                    "_verify_session_page",
                    fake_verify_session_page,
                ),
                patch.object(agent_runner, "ChatOpenAI", lambda **_kwargs: object()),
                patch.object(agent_runner, "Agent", FakeAgent),
                patch.object(
                    agent_runner.preview_manager,
                    "invalidate",
                    fake_invalidate,
                ),
                patch.object(agent_runner, "_terminate_process", no_op),
                patch.object(
                    agent_runner,
                    "send_completion_notification",
                    failing_notify,
                ),
            ):
                await agent_runner.run_job(
                    database,
                    EventHub(),
                    settings,
                    job["id"],
                )

            completed = database.get_job(job["id"])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(
                completed["submission_status"],
                "filled_pending_review",
            )
            event_kinds = [
                event["kind"]
                for event in database.list_events(job["id"])
            ]
            self.assertIn("completed", event_kinds)
            self.assertIn("notification_error", event_kinds)


if __name__ == "__main__":
    unittest.main()
