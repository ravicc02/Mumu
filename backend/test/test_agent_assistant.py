from __future__ import annotations

import dataclasses
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

from app.config import Settings
from app.db import Database
from app.services.agents.assistant import MumuAssistant
from app.services.agents.authorization import AuthorizationService
from app.services.agents.responder import AssistantDecision
from app.services.learning.store import LearningStore


class FakeOrchestrator:
    def __init__(self) -> None:
        self.wake_count = 0
        self.dispatch_count = 0

    def wake(self) -> None:
        self.wake_count += 1

    async def dispatch(self) -> dict:
        self.dispatch_count += 1
        return {"started": []}


class FakeLearningService:
    def __init__(self, database: Database) -> None:
        self.store = LearningStore(database)
        self.started: list[str] = []
        self.controls: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def start(self, course_id: str) -> dict:
        self.started.append(course_id)
        return self.store.create_run(course_id)

    async def control(self, identifier: str, action: str) -> dict:
        self.controls.append((identifier, action))
        return self.store.run(identifier)

    def delete_task(self, identifier: str) -> dict:
        self.deleted.append(identifier)
        return {"deleted_ids": [identifier], "task_kind": "learning"}


class AssistantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        LearningStore(self.database).initialize()
        self.learning = FakeLearningService(self.database)
        self.orchestrator = FakeOrchestrator()
        self.assistant = MumuAssistant(
            lambda: self.database,
            lambda: dataclasses.replace(
                self.settings,
                openai_api_key="fixture-model-key",
            ),
            learning_service=self.learning,
            orchestrator=self.orchestrator,
        )
        self.auth = AuthorizationService(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @property
    def academic_year(self) -> int:
        now = datetime.now()
        return now.year if now.month >= 8 else now.year - 1

    def account(self, name: str, platform: str = "chaoxing") -> dict:
        account = self.database.create_account(name, platform, self.settings)
        self.database.update_account(account["id"], status="ready")
        return self.database.get_account(account["id"])

    def assignment(
        self,
        account: dict,
        title: str,
        *,
        course: str = "软件工程",
        suffix: str = "one",
    ) -> dict:
        assignment_id = self.database.upsert_assignment(
            account["id"],
            account["platform"],
            {
                "academicYear": self.academic_year,
                "courseName": course,
                "courseId": f"course-{suffix}",
                "classId": f"class-{suffix}",
                "workId": f"work-{suffix}",
                "title": title,
                "status": "unfinished",
                "submittable": True,
                "taskUrl": f"https://example.test/work/{suffix}",
            },
            self.settings,
        )
        return self.database.get_assignment(assignment_id)

    def course(self, account: dict, name: str, suffix: str) -> dict:
        return LearningStore(self.database).upsert_courses(
            account["id"],
            [{
                "courseId": f"course-{suffix}",
                "classId": f"class-{suffix}",
                "courseName": name,
                "url": f"https://example.test/course/{suffix}",
            }],
            self.academic_year,
        )[0]

    def authorize(
        self,
        role: str,
        account_scope: list[str],
        *,
        external_user_id: str = "weixin-user",
        readonly: list[str] | None = None,
    ) -> None:
        self.auth.set_user(
            "weixin",
            external_user_id,
            role=role,
            account_scope=account_scope,
            readonly_account_ids=readonly or [],
            enabled=True,
            display_name="Fixture User",
        )

    async def send(self, text: str, message_id: str, user: str = "weixin-user"):
        return await self.assistant.handle_message(
            channel="weixin",
            external_user_id=user,
            text=text,
            inbound_message_id=message_id,
        )

    async def test_original_running_task_question_reads_status_instead_of_help(self):
        account = self.account("张三")
        self.authorize("viewer", [account["id"]])
        responder = AsyncMock(side_effect=AssertionError("Query must work without model"))
        self.assistant.general_responder = responder
        empty = await self.send("现在有在运行的任务吗", "running-empty")
        self.assertEqual(empty["tool"], "tasks.list")
        self.assertIn("运行中 0", empty["reply"])
        self.assertIn("排队 0", empty["reply"])

        assignment = self.assignment(account, "实验四", suffix="running")
        job = self.database.create_job(account["id"], assignment["id"], False)
        self.database.update_job(job["id"], status="running", phase="agent_running")
        foreign = self.account("范围外")
        secret = self.assignment(foreign, "不可泄漏的任务", suffix="foreign")
        self.database.create_job(foreign["id"], secret["id"], False)

        for index, text in enumerate(("现在有在运行的任务吗", "哪些任务在跑", "列出进行中的任务")):
            result = await self.send(text, f"running-query-{index}")
            self.assertEqual(result["status"], "ok")
            self.assertIn("运行中 1", result["reply"])
            self.assertIn("实验四", result["reply"])
            self.assertNotIn("不可泄漏", result["reply"])
        responder.assert_not_awaited()
        self.assertEqual(self.orchestrator.dispatch_count, 0)

    async def test_two_weixin_questions_create_two_factual_outbox_replies(self):
        from app.services.notifications.weixin_channel import WeixinChannel, ingest_updates

        account = self.account("张三")
        self.authorize("viewer", [account["id"]])
        ingest_updates(self.database, {"get_updates_buf": "cursor", "msgs": [
            {"message_id": identifier, "from_user_id": "weixin-user", "text": "现在有在运行的任务吗"}
            for identifier in ("wx-one", "wx-two")
        ]})
        channel = WeixinChannel(self.database, self.assistant)
        self.assertEqual(await channel.process_pending(), 2)
        self.assertEqual(await channel.process_pending(), 0)
        replies = channel.outbox.list_messages()
        self.assertEqual(len(replies), 2)
        self.assertTrue(all("运行中 0" in item["payload"]["text"] for item in replies))
        self.assertEqual(len(self.assistant._tools().list()), 2)

    async def test_model_intent_reads_real_tasks_and_ignores_fabricated_reply(self):
        account = self.account("张三")
        self.authorize("viewer", [account["id"]])
        self.assistant.general_responder = AsyncMock(return_value=AssistantDecision(
            intent="active_tasks", reply="模型编造：运行中 100 个任务",
        ))
        result = await self.send("手头还有什么事在忙？", "model-query")
        self.assertEqual(result["tool"], "tasks.list")
        self.assertIn("运行中 0", result["reply"])
        self.assertNotIn("100", result["reply"])

    async def test_model_write_proposal_still_requires_permission_and_confirmation(self):
        account = self.account("张三")
        self.authorize("operator", [account["id"]])
        assignment = self.assignment(account, "实验四", suffix="model-write")
        job = self.database.create_job(account["id"], assignment["id"], False)
        self.assistant.general_responder = AsyncMock(return_value=AssistantDecision(intent="pause"))
        preview = await self.send(f"让 {job['id']} 先歇一下", "model-pause")
        self.assertEqual(preview["status"], "confirmation_required")
        self.assertEqual(self.database.get_job(job["id"])["status"], "queued")
        self.authorize("viewer", [account["id"]])
        rejected = await self.send(f"让 {job['id']} 先歇一下", "model-pause-viewer")
        self.assertEqual(rejected["status"], "forbidden")
        self.assertEqual(self.database.get_job(job["id"])["status"], "queued")

    async def test_revoked_permission_is_checked_again_after_model_returns(self):
        account = self.account("张三")
        self.authorize("viewer", [account["id"]])

        async def revoke(_request):
            self.auth.delete_user("weixin", "weixin-user")
            return AssistantDecision(intent="active_tasks")

        self.assistant.general_responder = revoke
        result = await self.send("手头还有什么事在忙？", "revoked-query")
        self.assertEqual(result["status"], "forbidden")
        self.assertEqual(self.assistant._tools().list(), [])

    async def test_general_chat_uses_scrubbed_context_and_reports_model_failure(self):
        account = self.account("张三")
        self.authorize("viewer", [account["id"]])
        self.assistant.general_responder = AsyncMock(return_value=AssistantDecision(
            intent="chat", reply="你好，我是 Mumu。",
        ))
        result = await self.send("你好", "chat-one")
        self.assertEqual(result["tool"], "assistant.chat")
        self.assertEqual(result["reply"], "你好，我是 Mumu。")
        request = self.assistant.general_responder.call_args.args[0]
        self.assertNotIn("fixture-model-key", str(request))
        self.assertIn("context", request)
        self.assistant.general_responder = AsyncMock(side_effect=TimeoutError("fixture"))
        failed = await self.send("你能帮忙吗", "chat-error")
        self.assertEqual(failed["status"], "unavailable")
        self.assertIn("未执行任何操作", failed["reply"])

    async def test_invalid_model_intent_cannot_dispatch_arbitrary_tools(self):
        account = self.account("张三")
        self.authorize("admin", [account["id"]])
        self.assistant.general_responder = AsyncMock(return_value={"intent": "shell", "command": "unsafe"})
        failed = await self.send("帮我管理电脑", "invalid-model")
        self.assertEqual(failed["status"], "unavailable")
        self.assertEqual(self.assistant._tools().list(), [])

    async def test_unknown_confirmation_never_reaches_model(self):
        account = self.account("张三")
        self.authorize("admin", [account["id"]])
        responder = AsyncMock(return_value=AssistantDecision(intent="create_assignment"))
        self.assistant.general_responder = responder
        result = await self.send("确认上一个操作", "confirm-no-token")
        self.assertEqual(result["status"], "invalid")
        responder.assert_not_awaited()

    async def test_bound_user_keeps_session_after_identity_scrubbing_and_reset(self):
        account = self.account("张三")
        self.authorize("operator", [account["id"]])
        self.assistant.settings_provider = lambda: dataclasses.replace(
            self.settings, weixin_user_id="weixin-user",
        )
        first = await self.send("现在有在运行的任务吗", "session-one")
        second = await self.send("现在有在运行的任务吗", "session-two")
        self.assertEqual(first["session_id"], second["session_id"])
        reset = await self.send("/new", "session-new")
        self.assertNotEqual(reset["session_id"], first["session_id"])
        after = await self.send("现在有在运行的任务吗", "session-after")
        self.assertEqual(after["session_id"], reset["session_id"])

    async def test_unauthorized_user_is_rejected_before_conversation_or_tools(self):
        result = await self.send("/whoami", "unauthorized-1", "unknown")

        self.assertEqual(result["status"], "unauthorized")
        self.assertTrue(result["queued"])
        self.assertFalse(self.auth.get_user("weixin", "unknown")["enabled"])
        self.assertEqual(self.assistant._tools().list(), [])
        self.assertEqual(
            self.assistant._memory().find_active_session(
                agent_kind="assistant",
                channel="weixin",
                external_user_ref="unknown",
            ),
            None,
        )

    async def test_viewer_cannot_create_control_or_delete(self):
        account = self.account("张三")
        self.course(account, "软件工程", "viewer-course")
        self.authorize("viewer", [account["id"]])

        create = await self.send(
            "把张三的软件工程课程开启刷课",
            "viewer-create",
        )
        delete = await self.send("删除任务", "viewer-delete")

        self.assertEqual(create["status"], "forbidden")
        self.assertEqual(delete["status"], "forbidden")
        self.assertEqual(self.learning.started, [])
        self.assertEqual(self.assistant._confirmation().pending_count(
            channel="weixin",
            external_user_id="weixin-user",
            session_id=create["session_id"],
        ), 0)

    async def test_account_selection_forces_the_selected_stable_account(self):
        first = self.account("张三", "chaoxing")
        second = self.account("张三", "educoder")
        course = self.course(second, "软件工程", "selected-account")
        self.authorize("operator", ["*"])

        selection = await self.send(
            "把张三的软件工程课程开启刷课",
            "account-select",
        )
        self.assertEqual(selection["status"], "selection_required")
        session = self.assistant._memory().get_session(selection["session_id"])
        candidates = session["metadata"]["selection"]["candidates"]
        selected_index = next(
            index
            for index, item in enumerate(candidates, start=1)
            if item["id"] == second["id"]
        )

        result = await self.send(
            f"选择 {selected_index}",
            "account-select-confirm",
        )

        self.assertEqual(result["status"], "confirmation_required")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM pending_actions"
            ).fetchone()
        self.assertIn(second["id"], row["payload_json"])
        self.assertNotIn(first["id"], row["payload_json"])
        self.assertIn(course["id"], row["payload_json"])

    async def test_duplicate_course_names_require_selection(self):
        account = self.account("张三")
        first, second = LearningStore(self.database).upsert_courses(
            account["id"],
            [
                {
                    "courseId": "course-duplicate-a",
                    "classId": "class-duplicate-a",
                    "courseName": "软件工程",
                    "url": "https://example.test/course/duplicate-a",
                },
                {
                    "courseId": "course-duplicate-b",
                    "classId": "class-duplicate-b",
                    "courseName": "软件工程",
                    "url": "https://example.test/course/duplicate-b",
                },
            ],
            self.academic_year,
        )
        self.authorize("operator", [account["id"]])

        selection = await self.send(
            "把张三的软件工程课程开启刷课",
            "course-select",
        )
        self.assertEqual(selection["status"], "selection_required")
        session = self.assistant._memory().get_session(selection["session_id"])
        candidates = session["metadata"]["selection"]["candidates"]
        selected = next(item for item in candidates if item["id"] == second["id"])
        selected_index = candidates.index(selected) + 1

        result = await self.send(
            f"选择 {selected_index}",
            "course-select-confirm",
        )

        self.assertEqual(result["status"], "confirmation_required")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM pending_actions"
            ).fetchone()
        self.assertIn(second["id"], row["payload_json"])
        self.assertNotIn(first["id"], row["payload_json"])

    async def test_assignment_confirmation_creates_one_non_submitting_attempt(self):
        account = self.account("张三")
        assignment = self.assignment(account, "实验四", suffix="confirm")
        self.authorize("operator", [account["id"]])

        preview = await self.send(
            "把张三的作业 实验四 做了",
            "assignment-preview",
        )
        self.assertEqual(preview["status"], "confirmation_required")
        self.assertEqual(
            self.database.list_jobs(include_deleted=False),
            [],
        )
        token = preview["confirmation"]["token"]

        confirmed = await self.send(f"确认 {token}", "assignment-confirm")
        duplicate = await self.send(f"确认 {token}", "assignment-confirm-again")

        self.assertEqual(confirmed["status"], "ok")
        self.assertEqual(duplicate["status"], "duplicate")
        jobs = self.database.list_jobs(include_deleted=False)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["assignment_id"], assignment["id"])
        self.assertFalse(jobs[0]["auto_submit"])
        self.assertEqual(self.orchestrator.dispatch_count, 1)

    async def test_assignment_completion_question_is_read_only(self):
        account = self.account("张三")
        assignment = self.assignment(
            account,
            "实验四",
            suffix="completion-question",
        )
        job = self.database.create_job(account["id"], assignment["id"], False)
        self.authorize("viewer", [account["id"]])

        result = await self.send("实验四作业完成了吗", "completion-question")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool"], "tasks.get")
        self.assertEqual(self.database.get_job(job["id"])["status"], "queued")
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) count FROM pending_actions"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    async def test_confirmation_failure_is_actionable_and_does_not_create_task(self):
        account = self.account("张三")
        self.assignment(account, "实验四", suffix="confirmation-failure")
        self.authorize("operator", [account["id"]])
        preview = await self.send(
            "把张三的作业 实验四 做了",
            "confirmation-failure-preview",
        )
        self.database.update_account(account["id"], status="expired")

        result = await self.send(
            f"确认 {preview['confirmation']['token']}",
            "confirmation-failure-confirm",
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("账号授权已失效", result["reply"])
        self.assertIn("确认码已使用", result["reply"])
        self.assertEqual(self.database.list_jobs(include_deleted=False), [])

    async def test_progress_selection_never_becomes_a_stop_action(self):
        account = self.account("张三")
        first_assignment = self.assignment(
            account,
            "实验一",
            suffix="progress-a",
        )
        second_assignment = self.assignment(
            account,
            "实验二",
            suffix="progress-b",
        )
        first = self.database.create_job(account["id"], first_assignment["id"], False)
        second = self.database.create_job(account["id"], second_assignment["id"], False)
        self.authorize("viewer", [account["id"]])

        selection = await self.send("任务进度怎么样了", "progress-select")
        self.assertEqual(selection["status"], "selection_required")
        session = self.assistant._memory().get_session(selection["session_id"])
        candidates = session["metadata"]["selection"]["candidates"]
        self.assertEqual(session["metadata"]["selection"]["purpose"], "progress")
        desired = second["id"]
        selected_index = next(
            index
            for index, item in enumerate(candidates, start=1)
            if item["id"] == desired
        )

        result = await self.send(
            f"选择 {selected_index}",
            "progress-selected",
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool"], "tasks.get")
        self.assertEqual(self.learning.controls, [])
        self.assertEqual(
            self.database.get_job(first["id"])["status"],
            "queued",
        )
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) count FROM pending_actions"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    async def test_operator_cannot_delete_but_admin_can_delete_stopped_task(self):
        account = self.account("张三")
        assignment = self.assignment(account, "可删除", suffix="delete")
        job = self.database.create_job(account["id"], assignment["id"], False)
        self.database.update_job(job["id"], status="stopped", phase="stopped")
        self.authorize("operator", [account["id"]], external_user_id="operator")
        self.authorize("admin", [account["id"]], external_user_id="admin")

        forbidden = await self.send(
            f"删除任务 {job['id']}",
            "delete-operator",
            "operator",
        )
        preview = await self.send(
            f"删除任务 {job['id']}",
            "delete-admin",
            "admin",
        )

        self.assertEqual(forbidden["status"], "forbidden")
        self.assertEqual(preview["status"], "confirmation_required")
        confirmed = await self.send(
            f"确认 {preview['confirmation']['token']}",
            "delete-admin-confirm",
            "admin",
        )
        self.assertEqual(confirmed["status"], "ok")
        self.assertEqual(
            self.database.list_jobs(include_deleted=False),
            [],
        )

    async def test_learning_delete_uses_the_existing_delete_guard(self):
        account = self.account("张三")
        course = self.course(account, "软件工程", "learning-delete")
        run = LearningStore(self.database).create_run(course["id"])
        self.authorize("admin", [account["id"]])

        preview = await self.send(
            f"删除任务 {run['id']}",
            "learning-delete-preview",
        )
        confirmed = await self.send(
            f"确认 {preview['confirmation']['token']}",
            "learning-delete-confirm",
        )

        self.assertEqual(preview["status"], "confirmation_required")
        self.assertEqual(confirmed["status"], "ok")
        self.assertEqual(self.learning.deleted, [run["id"]])
        self.assertEqual(self.learning.controls, [])

    async def test_readonly_account_scope_rejects_write_before_confirmation(self):
        account = self.account("张三")
        self.course(account, "软件工程", "readonly")
        self.authorize(
            "operator",
            [account["id"]],
            readonly=[account["id"]],
        )

        result = await self.send(
            "把张三的软件工程课程开启刷课",
            "readonly-write",
        )

        self.assertEqual(result["status"], "forbidden")
        self.assertEqual(self.learning.started, [])
        self.assertEqual(self.assistant._confirmation().pending_count(
            channel="weixin",
            external_user_id="weixin-user",
            session_id=result["session_id"],
        ), 0)

    async def test_batch_writes_are_not_expanded_into_multiple_actions(self):
        account = self.account("张三")
        self.assignment(account, "实验一", suffix="batch")
        self.authorize("admin", [account["id"]])

        result = await self.send(
            "把所有作业全部执行",
            "batch-write",
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.database.list_jobs(include_deleted=False), [])
        self.assertEqual(self.orchestrator.dispatch_count, 0)


if __name__ == "__main__":
    unittest.main()
