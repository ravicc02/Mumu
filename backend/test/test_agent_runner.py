from __future__ import annotations

import tempfile
import unittest
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.db import Database
from app.events import EventHub
from app.services.tasks import runner as agent_runner


class FakeProcess:
    pid = 4242
    returncode = None

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return int(self.returncode or 0)


class FakeCurrentState:
    next_goal = "填写第一题"


class FakeClickAction:
    def model_dump(self, **_kwargs) -> dict:
        return {"index": 5}


class FakeOutput:
    current_state = FakeCurrentState()
    action = [FakeClickAction()]


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
        self.page = SimpleNamespace(session_id="test", evaluate=AsyncMock(return_value={"saved": True, "evidence": "platform_callback"}))
        self.cdp_client = SimpleNamespace(send=SimpleNamespace(Page=SimpleNamespace(
            getLayoutMetrics=AsyncMock(return_value={"cssContentSize": {"width": 1440, "height": 900}, "cssLayoutViewport": {"clientWidth": 1440, "clientHeight": 900}}),
            captureScreenshot=AsyncMock(return_value={"data": "cG5n"}),
        )))

    async def get_current_page(self):
        return self.page

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
        self.window_restored = False
        session = self

        class PageApi:
            async def bringToFront(self, **kwargs):
                session.called = kwargs.get("session_id") == "page-session"

        class BrowserApi:
            async def getWindowForTarget(self):
                return {"windowId": 7}

            async def setWindowBounds(self, params):
                session.window_restored = (
                    params["windowId"] == 7
                    and params["bounds"]["windowState"] == "normal"
                )

        class SendApi:
            Page = PageApi()
            Browser = BrowserApi()

        class CdpClient:
            send = SendApi()

        self.cdp_client = CdpClient()

    async def get_current_page(self):
        return FakeFocusPage()


class AgentRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        await agent_runner.close_preserved_sessions()

    def test_error_diagnostic_explains_model_and_browser_failures(self) -> None:
        model_error = agent_runner._diagnose_error(
            RuntimeError("Invalid JSON: validation error for AgentOutput"),
            stage="Agent 页面执行",
            current_url="https://example.test/work",
            last_action="点击元素 #12",
        )
        self.assertEqual(model_error["stage"], "模型输出")
        self.assertIn("最后页面", model_error["summary"])
        self.assertIn("建议", model_error["summary"])

        browser_error = agent_runner._diagnose_error(
            RuntimeError("CDP requests failed or timed out: ax_tree"),
            stage="Agent 页面执行",
            current_url="https://example.test/work",
            last_action="执行页面脚本",
        )
        self.assertEqual(browser_error["stage"], "浏览器页面")
        self.assertIn("页面读取或截图超时", browser_error["summary"])

    def test_agent_operation_error_preserves_explicit_diagnostic(self) -> None:
        diagnostic = agent_runner._diagnose_error(
            agent_runner.AgentOperationError(
                "页面填写与核验",
                "第 22 题没有写入",
                "进入浏览器补齐第 22 题",
            ),
            stage="未知",
            current_url="https://example.test/work",
            last_action="批量填写",
        )
        self.assertEqual(diagnostic["stage"], "页面填写与核验")
        self.assertEqual(diagnostic["reason"], "第 22 题没有写入")
        self.assertEqual(diagnostic["suggestion"], "进入浏览器补齐第 22 题")

    def test_normalizes_flattened_deepseek_action(self) -> None:
        parameters = {
            "$defs": {
                "ActionModelUnion": {
                    "anyOf": [
                        {"$ref": "#/$defs/ExtractActionModel"},
                        {"$ref": "#/$defs/SearchActionModel"},
                        {"$ref": "#/$defs/WriteFileActionModel"},
                    ],
                },
                "ExtractActionModel": {
                    "properties": {
                        "extract": {"$ref": "#/$defs/ExtractAction"},
                    },
                },
                "ExtractAction": {
                    "properties": {
                        "query": {"type": "string"},
                        "extract_links": {"type": "boolean"},
                    },
                    "required": ["query"],
                },
                "SearchActionModel": {
                    "properties": {
                        "search": {"$ref": "#/$defs/SearchAction"},
                    },
                },
                "SearchAction": {
                    "properties": {
                        "query": {"type": "string"},
                        "engine": {"type": "string"},
                    },
                    "required": ["query"],
                },
                "WriteFileActionModel": {
                    "properties": {
                        "write_file": {"$ref": "#/$defs/write_file_Params"},
                    },
                },
                "write_file_Params": {
                    "properties": {
                        "file_name": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["file_name", "content"],
                },
            },
            "required": ["memory", "action"],
        }
        normalized = agent_runner._normalize_flattened_action(
            {
                "query": "列出页面上的全部题目",
                "extract_links": False,
            },
            parameters,
        )
        self.assertEqual(normalized["action"][0]["extract"]["query"], "列出页面上的全部题目")
        self.assertIn("memory", normalized)

        file_action = agent_runner._normalize_flattened_action(
            {"file_name": "todo.md", "content": "- Q1"},
            parameters,
        )
        self.assertEqual(
            file_action["action"][0]["write_file"]["file_name"],
            "todo.md",
        )

    async def test_deepseek_uses_native_function_calling_model(self) -> None:
        captured = {}

        class CapturingDeepSeek:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        settings = Settings(
            openai_model="deepseek-v4.1-flash",
            openai_api_key="test-key",
            openai_base_url="https://example.test/v1",
            reasoning_effort="high",
        )
        with patch.object(
            agent_runner,
            "ReasoningDeepSeekChat",
            CapturingDeepSeek,
        ):
            agent_runner._build_agent_llm(
                settings,
                settings.openai_model,
                temperature=0.1,
                max_retries=1,
            )
        self.assertEqual(captured["model"], "deepseek-v4.1-flash")
        self.assertEqual(captured["reasoning_effort"], "high")
        self.assertEqual(captured["max_tokens"], 8192)
        self.assertIs(
            agent_runner.browser_use_utils.SignalHandler,
            agent_runner._NoopSignalHandler,
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
            self.assertTrue(session.window_restored)
        finally:
            agent_runner._job_sessions.pop("focus-job", None)

    async def test_capture_job_snapshot_uses_active_browser_session(self) -> None:
        async def fake_capture(*_args, **_kwargs):
            return b"png-data", "https://example.test/current"

        agent_runner._job_sessions["snapshot-job"] = object()
        try:
            with patch.object(
                agent_runner,
                "_capture_screenshot",
                fake_capture,
            ):
                snapshot = await agent_runner.capture_job_snapshot(
                    "snapshot-job"
                )
        finally:
            agent_runner._job_sessions.pop("snapshot-job", None)
        self.assertEqual(snapshot["url"], "https://example.test/current")
        self.assertEqual(
            snapshot["image"],
            "data:image/png;base64,cG5nLWRhdGE=",
        )

    async def test_page_init_scripts_include_webdriver_stealth(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.scripts = []

            async def _cdp_add_init_script(self, script):
                self.scripts.append(script)

        session = Session()
        await agent_runner._install_page_simplifier(session)
        self.assertEqual(len(session.scripts), 2)
        self.assertIn("webdriver", session.scripts[0])
        self.assertIn("edui-editor-toolbarbox", session.scripts[1])

    async def test_chrome_launch_avoids_unsupported_automation_flag(self) -> None:
        captured = {}

        async def fake_subprocess(*args, **kwargs):
            captured["args"] = args
            return FakeProcess()

        async def fake_wait_for_cdp(*_args, **_kwargs):
            return "ws://127.0.0.1:9999/devtools/browser/test"

        with (
            patch.object(
                agent_runner,
                "chrome_executable",
                lambda: Path("chrome.exe"),
            ),
            patch.object(
                agent_runner.asyncio,
                "create_subprocess_exec",
                fake_subprocess,
            ),
            patch.object(
                agent_runner,
                "_wait_for_cdp",
                fake_wait_for_cdp,
            ),
        ):
            await agent_runner._launch_chrome(
                "C:/temp/profile",
                headless=True,
                timeout=1,
            )
        self.assertNotIn(
            "--disable-blink-features=AutomationControlled",
            captured["args"],
        )

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

    async def test_filled_job_waits_for_review_with_logs_and_artifacts(self) -> None:
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
            self.assertEqual(completed["status"], "needs_intervention")
            self.assertEqual(completed["browser_retained"], 1)
            events = database.list_events(job["id"])
            saved_at = next(index for index, event in enumerate(events) if event["kind"] == "draft_saved")
            reviewed_at = next(index for index, event in enumerate(events) if event["kind"] == "needs_intervention")
            self.assertLess(saved_at, reviewed_at)
            final = next(event for event in events if event["kind"] == "final_screenshot")
            self.assertEqual(len(final["data"]["segments"]), 1)
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
                    "needs_intervention",
                }.issubset(event_kinds)
            )
            step_event = next(
                event
                for event in database.list_events(job["id"])
                if event["kind"] == "step"
            )
            self.assertEqual(
                step_event["data"]["action_details"],
                [{"type": "FakeClickAction", "data": {"index": 5}}],
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
                patch.object(agent_runner, "Agent", FakeIncompleteAgent),
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
            finished = await agent_runner.finish_job_intervention(
                database,
                EventHub(),
                job["id"],
            )
            self.assertEqual(finished["status"], "incomplete")
            self.assertEqual(finished["browser_retained"], 0)
            self.assertFalse(database.has_active_job(account["id"]))

    async def test_notification_failure_does_not_change_review_status(self) -> None:
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
            self.assertEqual(completed["status"], "needs_intervention")
            self.assertEqual(
                completed["submission_status"],
                "filled_pending_review",
            )
            event_kinds = [
                event["kind"]
                for event in database.list_events(job["id"])
            ]
            self.assertIn("needs_intervention", event_kinds)
            self.assertIn("notification_error", event_kinds)

    async def test_run_outcomes_require_submission_and_preserve_manual_review(self) -> None:
        cases = [
            (False, True, False, "已填写全部题目", "needs_intervention"),
            (True, True, True, "已提交", "completed"),
            (True, True, False, "已填写全部题目", "incomplete"),
            (False, False, False, "缺少上传附件，无法完成第 3 题", "incomplete"),
            (False, False, False, "上游 timeout，模型请求失败", "interrupted"),
            (False, False, False, "network 拓扑图缺少附件，无法完成第 3 题", "incomplete"),
            (False, False, False, "RAISE: upstream API error 503", "interrupted"),
            (False, True, False, "SAVE_FAIL: 已填写但平台拒绝暂存", "incomplete"),
        ]
        for auto_submit, success, submitted, message, expected in cases:
            with self.subTest(expected=expected, auto_submit=auto_submit), tempfile.TemporaryDirectory() as root:
                settings = Settings(data_root=Path(root), openai_api_key="test", openai_model="test-model")
                settings.ensure_directories()
                database = Database(settings)
                database.initialize()
                account = database.create_account("test", "chaoxing", settings)
                assignment_id = database.upsert_assignment(account["id"], "chaoxing", {
                    "title": "test", "taskUrl": "https://example.test/work", "workId": "test",
                    "courseId": "course", "classId": "class", "submittable": True,
                }, settings)
                job = database.create_job(account["id"], assignment_id, auto_submit, True)

                class OutcomeHistory(FakeHistory):
                    def final_result(self):
                        return message

                    def is_successful(self):
                        return success

                class OutcomeAgent(FakeAgent):
                    async def run(self, max_steps, on_step_start):
                        await on_step_start(self)
                        if message.startswith("RAISE:"):
                            raise RuntimeError(message)
                        await self.on_step(None, FakeOutput(), 0)
                        return OutcomeHistory()

                with (
                    patch.object(agent_runner, "_launch_chrome", AsyncMock(return_value=(FakeProcess(), "ws://test"))),
                    patch.object(agent_runner, "_capture_screenshot", AsyncMock(return_value=(b"png", "https://example.test/work"))),
                    patch.object(agent_runner, "BrowserSession", FakeBrowserSession),
                    patch.object(agent_runner, "_restore_storage_state_to_session", AsyncMock(return_value=1)),
                    patch.object(agent_runner, "_verify_session_page", fake_verify_session_page),
                    patch.object(agent_runner, "_run_structured_chaoxing_quiz", AsyncMock(return_value={"unit_count": 1} if auto_submit else None)),
                    patch.object(agent_runner, "save_assignment_draft", AsyncMock(return_value={"saved": True}, side_effect=agent_runner.DraftSaveError("暂时保存失败") if message.startswith("SAVE_FAIL:") else None)),
                    patch.object(agent_runner, "_submission_evidence", AsyncMock(return_value={"confirmed": submitted})),
                    patch.object(agent_runner, "_build_agent_llm", lambda *args, **kwargs: object()),
                    patch.object(agent_runner, "Agent", OutcomeAgent),
                    patch.object(agent_runner, "_terminate_process", AsyncMock()),
                    patch.object(agent_runner, "send_completion_notification", AsyncMock(return_value={})),
                ):
                    hub = EventHub()
                    await agent_runner.run_job(database, hub, settings, job["id"])
                    result = database.get_job(job["id"])
                    self.assertEqual(result["status"], expected)
                    self.assertEqual(bool(result["browser_retained"]), expected in {"needs_intervention", "incomplete"})
                    if message.startswith("SAVE_FAIL:"):
                        self.assertIn("暂时保存失败", result["error"])
                        self.assertNotIn("draft_saved", [event["kind"] for event in database.list_events(job["id"])])
                    if expected == "completed":
                        self.assertEqual(result["submission_status"], "submitted_verified")
                        self.assertEqual(result["progress"], 100)
                    if expected == "needs_intervention":
                        closed = await agent_runner.finish_job_intervention(database, hub, job["id"])
                        self.assertEqual(closed["status"], "needs_intervention")
                        database.mark_interrupted_jobs()
                        self.assertEqual(database.get_job(job["id"])["status"], "needs_intervention")
                        reviewed = await agent_runner.review_job_submission(database, hub, job["id"], "submitted_verified")
                        self.assertEqual(reviewed["status"], "completed")
                    if expected == "incomplete":
                        reviewed = await agent_runner.review_job_submission(database, hub, job["id"], "submitted_verified")
                        self.assertEqual(reviewed["status"], "completed")
                        self.assertFalse(reviewed["browser_available"])
                        self.assertFalse(database.has_active_job(account["id"]))
                    await agent_runner.close_preserved_sessions()


if __name__ == "__main__":
    unittest.main()
