from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.services.agents.memory import MemoryService
from app.services.agents.workspace import TaskWorkspace


async def _summary_result(messages):
    return {"summary": f"共 {len(messages)} 条消息"}


class AgentMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        self.memory = MemoryService(self.database, self.settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_session_messages_and_summary_survive_service_recreation(self):
        session = self.memory.create_session(
            agent_kind="assistant",
            channel="desktop",
            title="测试对话",
        )
        first = self.memory.append_message(
            session_id=session["id"],
            role="user",
            content={"text": "帮我查看任务"},
        )
        second = self.memory.append_message(
            session_id=session["id"],
            role="assistant",
            content={"text": "正在读取任务总况"},
        )
        summary = self.memory.save_summary(
            session_id=session["id"],
            from_sequence=first["sequence"],
            to_sequence=second["sequence"],
            summary="用户请求查看任务总况。",
        )

        restarted = MemoryService(Database(self.settings), self.settings)
        recovered = restarted.recover_session(session["id"])

        self.assertEqual(first["sequence"], 1)
        self.assertEqual(second["sequence"], 2)
        self.assertEqual(summary["to_sequence"], 2)
        self.assertEqual(recovered["session"]["title"], "测试对话")
        self.assertEqual(
            recovered["summary"]["summary"],
            "用户请求查看任务总况。",
        )

    def test_summarizer_adapter_stores_only_derived_summary(self):
        session = self.memory.create_session(
            agent_kind="assistant",
            channel="desktop",
        )
        self.memory.append_message(
            session_id=session["id"],
            role="user",
            content={"text": "先检查实验四"},
        )
        self.memory.append_message(
            session_id=session["id"],
            role="assistant",
            content={"text": "正在检查"},
        )

        summary = asyncio.run(self.memory.summarize_session(
            session_id=session["id"],
            from_sequence=1,
            to_sequence=2,
            summarizer=lambda messages: _summary_result(messages),
        ))

        self.assertEqual(summary["summary"], "共 2 条消息")
        self.assertEqual(
            self.memory.latest_summary(session["id"])["to_sequence"],
            2,
        )

    def test_long_term_memory_requires_safe_source_and_scrubs_secrets(self):
        with self.assertRaises(ValueError):
            self.memory.remember(
                scope="global",
                kind="preference",
                content="模型猜测用户喜欢深色模式",
                source_ref="model-output",
                source_kind="model_inference",  # type: ignore[arg-type]
            )

        item = self.memory.remember(
            scope="account:abc",
            kind="preference",
            content="用户说以后使用 api_key=abcdefghijk 时不要显示明文",
            source_ref="message:1",
            source_kind="explicit_user",
        )

        self.assertNotIn("abcdefghijk", item["content"])
        self.assertTrue(item["sensitive"])
        self.assertEqual(
            self.memory.search_memories(
                scope="account:abc",
                query="明文",
            ),
            [],
        )
        visible = self.memory.search_memories(
            scope="account:abc",
            query="明文",
            include_sensitive=True,
        )
        self.assertEqual(len(visible), 1)

    def test_context_budget_keeps_mandatory_checkpoint_and_recent_messages(self):
        session = self.memory.create_session(
            agent_kind="assistant",
            channel="desktop",
        )
        for index in range(20):
            self.memory.append_message(
                session_id=session["id"],
                role="user",
                content={"text": f"历史消息 {index} " + ("内容" * 500)},
            )
        self.memory.save_summary(
            session_id=session["id"],
            from_sequence=1,
            to_sequence=10,
            summary="前十条历史消息的压缩摘要。",
        )
        self.memory.remember(
            scope="global",
            kind="preference",
            content="回答任务状态时先给出已完成和阻塞项。",
            source_ref="message:preference",
            source_kind="explicit_user",
        )
        checkpoint = self.database.save_task_checkpoint(
            attempt_id="attempt-context",
            attempt_kind="assignment",
            phase="waiting_upstream",
            goal={"assignment": "实验四"},
            completed={"progress": 40},
            blockers=["模型上游限流"],
            next_action_id="retry-model",
        )

        package = self.memory.assemble(
            scope="global",
            query="任务状态",
            session_id=session["id"],
            task_attempt_ids=["attempt-context"],
            system_context={"permission": "viewer"},
            token_budget=700,
        )

        kinds = [block.kind for block in package.blocks]
        task_block = next(
            block for block in package.blocks if block.kind == "task_checkpoint"
        )
        self.assertIn("system_identity", kinds)
        self.assertIn("session_summary", kinds)
        self.assertIn("阻塞", task_block.content)
        self.assertIn("实验四", task_block.content)
        self.assertLessEqual(package.estimated_tokens, 700)
        self.assertLess(len(package.messages), 20)
        self.assertGreater(package.dropped["messages"], 0)
        self.assertEqual(checkpoint["phase"], "waiting_upstream")

    def test_forget_and_session_delete_leave_tombstones_without_body(self):
        first = self.memory.remember(
            scope="global",
            kind="preference",
            content="记住第一条偏好",
            source_ref="message:one",
            source_kind="explicit_user",
        )
        second = self.memory.remember(
            scope="global",
            kind="habit",
            content="记住第二条习惯",
            source_ref="message:two",
            source_kind="confirmed_habit",
        )
        self.assertEqual(self.memory.forget_memory(memory_id=first["id"]), 1)
        self.assertEqual(
            [item["id"] for item in self.memory.search_memories(scope="global")],
            [second["id"]],
        )

        session = self.memory.create_session(
            agent_kind="assistant",
            channel="desktop",
        )
        self.memory.append_message(
            session_id=session["id"],
            role="user",
            content={"text": "这条消息应被删除"},
        )
        self.memory.delete_session(session["id"])

        with self.assertRaises(RuntimeError):
            self.memory.append_message(
                session_id=session["id"],
                role="user",
                content={"text": "不能继续写入"},
            )
        with self.database.connect() as connection:
            messages = connection.execute(
                "SELECT COUNT(*) count FROM agent_messages WHERE session_id=?",
                (session["id"],),
            ).fetchone()["count"]
            tombstones = connection.execute(
                "SELECT target_kind,target_id FROM memory_tombstones"
            ).fetchall()
        self.assertEqual(messages, 0)
        self.assertIn(("memory_item", first["id"]), {
            (row["target_kind"], row["target_id"]) for row in tombstones
        })
        self.assertIn(("agent_session", session["id"]), {
            (row["target_kind"], row["target_id"]) for row in tombstones
        })

    def test_prune_removes_old_messages_and_expired_memory_but_keeps_summary(self):
        session = self.memory.create_session(
            agent_kind="assistant",
            channel="desktop",
        )
        first = self.memory.append_message(
            session_id=session["id"],
            role="user",
            content={"text": "旧消息"},
        )
        second = self.memory.append_message(
            session_id=session["id"],
            role="assistant",
            content={"text": "摘要保留"},
        )
        self.memory.save_summary(
            session_id=session["id"],
            from_sequence=1,
            to_sequence=2,
            summary="旧消息摘要",
        )
        expired = self.memory.remember(
            scope="global",
            kind="fact",
            content="已经过期的记忆",
            source_ref="verified:one",
            source_kind="verified_fact",
            expires_at=(
                datetime.now(timezone.utc) - timedelta(days=1)
            ).isoformat(),
        )
        old_time = (
            datetime.now(timezone.utc) - timedelta(days=60)
        ).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE agent_messages SET created_at=? WHERE id IN (?,?)",
                (old_time, first["id"], second["id"]),
            )

        result = self.memory.prune(
            conversation_retention_days=30,
            memory_retention_days=365,
        )

        self.assertEqual(result["messages"], 2)
        self.assertEqual(result["memories"], 1)
        self.assertIsNotNone(self.memory.latest_summary(session["id"]))
        with self.assertRaises(KeyError):
            self.memory.get_memory(expired["id"])

    def test_deleting_task_removes_checkpoint_and_searchable_memory(self):
        account = self.database.create_account(
            "fixture",
            "chaoxing",
            self.settings,
        )
        assignment_id = self.database.upsert_assignment(
            account["id"],
            "chaoxing",
            {
                "academicYear": 2026,
                "courseName": "课程",
                "courseId": "course",
                "classId": "class",
                "workId": "work",
                "title": "实验",
                "status": "unfinished",
                "submittable": True,
                "taskUrl": "https://example.test/work",
            },
            self.settings,
        )
        job = self.database.create_job(account["id"], assignment_id, False)
        self.database.transition_job(job["id"], "stopped")
        self.database.save_task_checkpoint(
            attempt_id=job["id"],
            attempt_kind="assignment",
            phase="stopped",
            goal={"assignment": "实验"},
        )
        item = self.memory.remember(
            scope=f"attempt:{job['id']}",
            kind="fact",
            content="该任务的记忆正文",
            source_ref=job["id"],
            source_kind="verified_fact",
        )

        self.database.delete_job_task(job["id"])

        self.assertIsNone(self.database.latest_task_checkpoint(job["id"]))
        with self.assertRaises(KeyError):
            self.memory.get_memory(item["id"])
        with self.database.connect() as connection:
            tombstone = connection.execute(
                """
                SELECT 1 FROM memory_tombstones
                WHERE target_kind='task_attempt' AND target_id=?
                """,
                (job["id"],),
            ).fetchone()
        self.assertTrue(tombstone)

    def test_workspace_business_cleanup_preserves_evidence_directory(self):
        account = self.database.create_account(
            "workspace",
            "chaoxing",
            self.settings,
        )
        workspace = TaskWorkspace.for_account(
            account,
            "assignment",
            "attempt-workspace",
        )
        (workspace.input_dir / "input.txt").write_text(
            "input",
            encoding="utf-8",
        )
        (workspace.evidence_dir / "receipt.txt").write_text(
            "evidence",
            encoding="utf-8",
        )
        workspace.write_checkpoint({
            "version": 1,
            "attempt_kind": "assignment",
            "phase": "stopped",
            "goal": [],
            "completed": [],
            "blockers": [],
            "evidence_refs": [],
            "next_action_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        removed = TaskWorkspace.purge_business_data_for_account(
            account,
            "assignment",
            "attempt-workspace",
        )

        self.assertTrue(removed)
        self.assertFalse(workspace.manifest_path.exists())
        self.assertFalse(workspace.checkpoints_dir.exists())
        self.assertFalse(workspace.input_dir.exists())
        self.assertTrue((workspace.evidence_dir / "receipt.txt").is_file())


if __name__ == "__main__":
    unittest.main()
