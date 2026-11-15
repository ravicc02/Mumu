from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.services.agents.authorization import AuthorizationService
from app.services.agents.confirmation import ConfirmationService
from app.services.agents.eventing import IncidentCenter, OutboxService
from app.services.agents.tools import ToolCallConflict, ToolLedger


class AgentControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_authorization_applies_role_scope_readonly_and_single_identity(self):
        auth = AuthorizationService(self.database)
        pending = auth.ensure_user("weixin", "external-user")
        self.assertFalse(pending["enabled"])
        self.assertEqual(pending["role"], "viewer")

        user = auth.set_user(
            "weixin",
            "external-user",
            role="operator",
            account_scope=["account-a"],
            readonly_account_ids=["account-a"],
            enabled=True,
            display_name="Operator",
        )
        self.assertTrue(auth.check(
            channel="weixin",
            external_user_id="external-user",
            action="read",
            account_id="account-a",
        )[0])
        self.assertFalse(auth.check(
            channel="weixin",
            external_user_id="external-user",
            action="create",
            account_id="account-a",
        )[0])
        self.assertFalse(auth.check(
            channel="weixin",
            external_user_id="external-user",
            action="control",
            account_id="account-b",
        )[0])
        self.assertFalse(auth.check(
            channel="weixin",
            external_user_id="external-user",
            action="delete",
            account_id="account-a",
        )[0])

        admin = auth.set_user(
            "weixin",
            "admin-user",
            role="admin",
            account_scope=["*"],
            enabled=True,
        )
        self.assertEqual(admin["account_scope"], ["*"])
        self.assertTrue(auth.check(
            channel="weixin",
            external_user_id="admin-user",
            action="delete",
            account_id="any-account",
        )[0])
        with self.assertRaises(ValueError):
            auth.set_user(
                "weixin",
                "bad-scope",
                role="viewer",
                account_scope=["*", "account-a"],
            )
        self.assertEqual(user["display_name"], "Operator")

    def test_confirmation_is_bound_to_actor_and_session_and_consumed_once(self):
        service = ConfirmationService(self.database, self.settings)
        issued = service.issue(
            channel="weixin",
            external_user_id="user-a",
            session_id="session-a",
            inbound_message_id="message-a",
            action_kind="task_control",
            payload={"action": "pause", "task_id": "task-a"},
            idempotency_key="unique-confirmation",
        )

        wrong_user = service.consume(
            channel="weixin",
            external_user_id="user-b",
            session_id="session-a",
            token=issued["token"],
        )
        wrong_session = service.consume(
            channel="weixin",
            external_user_id="user-a",
            session_id="session-b",
            token=issued["token"],
        )
        confirmed = service.consume(
            channel="weixin",
            external_user_id="user-a",
            session_id="session-a",
            token=issued["token"],
        )
        repeated = service.consume(
            channel="weixin",
            external_user_id="user-a",
            session_id="session-a",
            token=issued["token"],
        )

        self.assertEqual(wrong_user.status, "invalid")
        self.assertEqual(wrong_session.status, "invalid")
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.payload["action"], "pause")
        self.assertEqual(repeated.status, "already_consumed")

    def test_confirmation_cancel_and_expiry_are_terminal(self):
        service = ConfirmationService(self.database, self.settings)
        cancelled = service.issue(
            channel="weixin",
            external_user_id="user-a",
            session_id="session-a",
            inbound_message_id="message-cancel",
            action_kind="task_delete",
            payload={"task_id": "task-a"},
            idempotency_key="cancel-confirmation",
        )
        expired = service.issue(
            channel="weixin",
            external_user_id="user-a",
            session_id="session-a",
            inbound_message_id="message-expired",
            action_kind="task_delete",
            payload={"task_id": "task-b"},
            idempotency_key="expired-confirmation",
        )
        self.assertTrue(service.cancel(
            channel="weixin",
            external_user_id="user-a",
            session_id="session-a",
            token=cancelled["token"],
        ))
        self.assertEqual(service.consume(
            channel="weixin",
            external_user_id="user-a",
            session_id="session-a",
            token=cancelled["token"],
        ).status, "cancelled")

        past = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE pending_actions SET expires_at = ?
                WHERE id = ?
                """,
                (past, expired["id"]),
            )
        self.assertEqual(service.consume(
            channel="weixin",
            external_user_id="user-a",
            session_id="session-a",
            token=expired["token"],
        ).status, "expired")

    async def test_outbox_deduplicates_claims_recovers_and_retries(self):
        outbox = OutboxService(self.database, self.settings)
        first = outbox.enqueue(
            channel="weixin",
            external_user_id="user-a",
            dedupe_key="reply:one",
            payload={"kind": "assistant_reply", "text": "hello"},
        )
        duplicate = outbox.enqueue(
            channel="weixin",
            external_user_id="user-a",
            dedupe_key="reply:one",
            payload={"kind": "assistant_reply", "text": "different"},
        )
        self.assertEqual(first["id"], duplicate["id"])

        claimed = outbox.claim_ready(limit=1)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["state"], "sending")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE outbox_messages
                SET next_attempt_at = '2000-01-01T00:00:00+00:00'
                WHERE id = ?
                """,
                (first["id"],),
            )
        self.assertEqual(outbox.recover_stale(), 1)

        attempted: list[str] = []

        async def failing_sender(message):
            attempted.append(message["id"])
            raise RuntimeError("network unavailable")

        result = await outbox.dispatch(failing_sender, limit=1)
        self.assertEqual(result, {"sent": 0, "failed": 1})
        self.assertEqual(attempted, [first["id"]])
        failed = outbox.list_messages(state="pending")[0]
        self.assertEqual(failed["attempts"], 1)
        self.assertEqual(failed["last_error"], "RuntimeError")

        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE outbox_messages
                SET next_attempt_at = '2000-01-01T00:00:00+00:00'
                WHERE id = ?
                """,
                (first["id"],),
            )

        async def successful_sender(message):
            attempted.append(message["id"])

        result = await outbox.dispatch(successful_sender, limit=1)
        self.assertEqual(result, {"sent": 1, "failed": 0})
        self.assertEqual(outbox.list_messages(state="sent")[0]["attempts"], 2)
        self.assertEqual(attempted, [first["id"], first["id"]])

    def test_incident_notifies_only_on_open_escalation_and_resolution(self):
        outbox = OutboxService(self.database, self.settings)
        incidents = IncidentCenter(self.database, outbox, self.settings)
        opened = incidents.open(
            dedupe_key="provider:default",
            severity="warning",
            title="模型服务异常",
            details="默认模型连续失败",
            next_action="等待冷却或切换兼容模型",
            recipients=["admin"],
        )
        incidents.open(
            dedupe_key="provider:default",
            severity="warning",
            title="模型服务异常",
            details="仍在失败",
            recipients=["admin"],
        )
        escalated = incidents.open(
            dedupe_key="provider:default",
            severity="critical",
            title="模型服务不可用",
            details="兼容模型也不可用",
            recipients=["admin"],
        )
        resolved = incidents.resolve(
            "provider:default",
            details="模型服务恢复",
            recipients=["admin"],
        )
        incidents.resolve(
            "provider:default",
            details="重复恢复",
            recipients=["admin"],
        )

        self.assertEqual(opened["status"], "open")
        self.assertEqual(escalated["severity"], "critical")
        self.assertEqual(resolved["status"], "resolved")
        messages = outbox.list_messages(limit=20)
        self.assertEqual(len(messages), 3)
        self.assertEqual(
            [item["payload"]["kind"] for item in reversed(messages)],
            ["incident", "incident", "incident_resolved"],
        )

    async def test_tool_ledger_replays_success_and_does_not_rerun_failures(self):
        ledger = ToolLedger(self.database, self.settings)
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            return {"value": 7}

        first = await ledger.execute(
            tool="fixture.read",
            arguments={"id": "one"},
            operation=operation,
            channel="weixin",
            external_user_id="user-a",
            idempotency_key="read-one",
        )
        replay = await ledger.execute(
            tool="fixture.read",
            arguments={"id": "one"},
            operation=operation,
            channel="weixin",
            external_user_id="user-a",
            idempotency_key="read-one",
        )
        self.assertEqual(first, replay)
        self.assertEqual(calls, 1)

        async def failing_operation():
            raise RuntimeError("fixture failure")

        with self.assertRaises(RuntimeError):
            await ledger.execute(
                tool="fixture.write",
                arguments={"id": "two"},
                operation=failing_operation,
                idempotency_key="write-two",
            )
        with self.assertRaises(ToolCallConflict):
            await ledger.execute(
                tool="fixture.write",
                arguments={"id": "two"},
                operation=failing_operation,
                idempotency_key="write-two",
            )
        rows = ledger.list(limit=10)
        self.assertEqual(
            {item["status"] for item in rows},
            {"succeeded", "error"},
        )


if __name__ == "__main__":
    unittest.main()
