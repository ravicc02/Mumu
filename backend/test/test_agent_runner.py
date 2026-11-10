from __future__ import annotations

import tempfile
import unittest
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

    def model_dump(self, mode: str = "json") -> dict:
        return {"mode": mode, "steps": 1}


class FakeAgent:
    def __init__(self, **kwargs):
        self.on_step = kwargs["register_new_step_callback"]

    async def run(self, max_steps: int, on_step_start) -> FakeHistory:
        await on_step_start(self)
        await self.on_step(None, FakeOutput(), 0)
        return FakeHistory()


class FakeBrowserSession:
    def __init__(self, **_kwargs):
        pass

    async def start(self) -> None:
        return None

    async def kill(self) -> None:
        return None

    async def get_current_page_url(self) -> str:
        return "https://example.test/assignment"


class AgentRunnerTests(unittest.IsolatedAsyncioTestCase):
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
                return None

            hub = EventHub()
            with (
                patch.object(agent_runner, "_launch_chrome", fake_launch),
                patch.object(
                    agent_runner,
                    "_capture_screenshot",
                    fake_capture,
                ),
                patch.object(agent_runner, "BrowserSession", FakeBrowserSession),
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


if __name__ == "__main__":
    unittest.main()
