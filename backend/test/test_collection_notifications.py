from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app import main
from app.config import Settings
from app.db import Database
from app.services.collection_notifications import initialize_baseline, ledger_key, notify_new_assignments
from app.services.notifier import _weixin_text, notification_preferences, send_completion_notification


class CollectionNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.root.name))
        self.settings.ensure_directories()
        self.db = Database(self.settings)
        self.db.initialize()
        self.account = self.create_account("one")
        self.db.set_setting("notification_preferences", {"new_assignments_enabled": True, "completion_enabled": True})

    async def asyncTearDown(self):
        self.root.cleanup()

    def create_account(self, name):
        account = self.db.create_account(name, "chaoxing", self.settings)
        self.db.update_account(account["id"], status="ready")
        return self.db.get_account(account["id"])

    def item(self, identifier="new"):
        return {"id": identifier, "status": "unfinished", "submittable": True,
                "course_name": "计算机科学", "title": f"作业 {identifier}", "deadline_text": "11-30 23:59"}

    async def notify(self, items, sender, scheduled=True, account=None):
        account = account or self.account
        initialize_baseline(self.db, account["id"], 2026)
        return await notify_new_assignments(self.db, self.settings, account, 2026, items, scheduled=scheduled, sender=sender)

    async def test_new_assignments_are_deduplicated_by_account_and_survive_reload(self):
        sender = AsyncMock(return_value={"weixin": True})
        await self.notify([self.item()], sender)
        await self.notify([self.item()], sender)
        self.assertEqual(sender.await_count, 1)
        other = self.create_account("two")
        await self.notify([self.item()], sender, account=other)
        self.assertEqual(sender.await_count, 2)
        reloaded = Database(self.settings)
        self.assertEqual(reloaded.get_setting(ledger_key(self.account["id"], 2026))["pending"], {})

    async def test_failed_push_is_retried_without_turning_collection_into_failure(self):
        sender = AsyncMock(side_effect=[RuntimeError("network unavailable"), {"weixin": True}])
        first = await self.notify([self.item()], sender)
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["pending_count"], 1)
        second = await self.notify([self.item()], sender)
        self.assertEqual(second["status"], "sent")
        self.assertEqual(second["new_count"], 0)
        self.assertEqual(second["pending_count"], 0)
        await self.notify([self.item()], sender)
        self.assertEqual(sender.await_count, 2)

    async def test_manual_collection_and_disabled_push_update_baseline_without_sending(self):
        sender = AsyncMock(return_value={"weixin": True})
        await self.notify([self.item()], sender, scheduled=False)
        await self.notify([self.item()], sender)
        self.db.set_setting("notification_preferences", {"new_assignments_enabled": False})
        await self.notify([self.item(), self.item("other")], sender)
        self.db.set_setting("notification_preferences", {"new_assignments_enabled": True})
        await self.notify([self.item(), self.item("other")], sender)
        sender.assert_not_awaited()

    async def test_pending_but_no_longer_submittable_assignment_is_not_pushed(self):
        sender = AsyncMock(side_effect=RuntimeError("offline"))
        await self.notify([self.item()], sender)
        result = await self.notify([{**self.item(), "submittable": False}], sender)
        self.assertEqual(result["pending_count"], 0)
        self.assertEqual(sender.await_count, 1)

    async def test_preferences_disable_network_delivery_before_creating_http_client(self):
        with patch("app.services.notifier.httpx.AsyncClient") as client:
            for event in ("assignment_completed", "assignment_needs_intervention", "assignment_interrupted", "assignments_discovered", "learning_completed", "learning_interrupted"):
                result = await send_completion_notification(self.settings, {"event": event}, preferences={"completion_enabled": False, "new_assignments_enabled": False})
                self.assertFalse(any(result.values()))
            client.assert_not_called()

    async def test_connector_get_and_partial_preference_patch_preserve_binding(self):
        binding = {"token": "secret-token", "user_id": "private-user", "context_token": "secret-context"}
        self.db.set_setting("weixin_binding", binding)
        with patch.object(main, "database", self.db), patch.object(main.weixin_connector, "begin_binding", AsyncMock()) as qr:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                status = (await client.get("/api/connectors/weixin")).json()
                self.assertTrue(status["configured"] and status["ready"])
                self.assertNotIn("secret-token", json.dumps(status))
                response = await client.patch("/api/connectors/weixin/preferences", json={"completion_enabled": False})
                self.assertTrue(response.json()["new_assignments_enabled"])
                self.assertFalse(response.json()["completion_enabled"])
                reloaded = (await client.get("/api/connectors/weixin")).json()
                self.assertFalse(reloaded["preferences"]["completion_enabled"])
            self.assertEqual(self.db.get_setting("weixin_binding"), binding)
            qr.assert_not_awaited()

    async def test_http_collections_can_overlap_across_accounts_but_not_within_one_account(self):
        other = self.create_account("two")
        entered = {identifier: asyncio.Event() for identifier in (self.account["id"], other["id"])}
        released = {identifier: asyncio.Event() for identifier in entered}
        async def collect(database, account_id, year, **kwargs):
            entered[account_id].set()
            await released[account_id].wait()
            return {"assignments": [], "total_courses": 1, "processed_courses": 1, "failed_courses": 0, "failed_course_ids": [], "errors": []}
        platform = SimpleNamespace(collect_assignments=collect)
        with patch.object(main, "database", self.db), patch.object(main, "effective_settings", return_value=self.settings), patch.object(main.platform_registry, "get", return_value=platform), patch.object(main.preview_manager, "is_interactive", AsyncMock(return_value=False)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                first = asyncio.create_task(client.post(f"/api/accounts/{self.account['id']}/collect?academic_year=2026"))
                second = None
                try:
                    await asyncio.wait_for(entered[self.account["id"]].wait(), 1)
                    second = asyncio.create_task(client.post(f"/api/accounts/{other['id']}/collect?academic_year=2026"))
                    await asyncio.wait_for(entered[other["id"]].wait(), 1)
                    duplicate = await client.post(f"/api/accounts/{self.account['id']}/collect")
                    self.assertEqual(duplicate.status_code, 409)
                    released[self.account["id"]].set()
                    self.assertEqual((await first).status_code, 200)
                    self.assertFalse(second.done())
                finally:
                    for event in released.values(): event.set()
                    results = await asyncio.gather(*[task for task in (first, second) if task is not None])
                self.assertTrue(all(response.status_code == 200 for response in results))

    async def test_scheduler_dispatches_accounts_together_and_does_not_duplicate_inflight_work(self):
        other = self.create_account("two")
        for account in (self.account, other): self.db.update_account(account["id"], auto_collect_enabled=1)
        gate, both = asyncio.Event(), asyncio.Event()
        started = []
        async def collect(account_id, year, *, scheduled):
            self.assertTrue(scheduled)
            started.append(account_id)
            if len(started) == 2: both.set()
            await gate.wait()
        with patch.object(main, "database", self.db), patch.object(main, "_scheduled_collection_tasks", {}), patch.object(main, "perform_collection", side_effect=collect), patch.object(main.preview_manager, "is_interactive", AsyncMock(return_value=False)):
            try:
                await main.schedule_due_collections()
                await asyncio.wait_for(both.wait(), 1)
                await main.schedule_due_collections()
                self.assertEqual(len(started), 2)
            finally:
                tasks = list(main._scheduled_collection_tasks.values())
                gate.set()
                await asyncio.gather(*tasks)

    async def test_scheduled_collection_sends_only_genuinely_new_verified_assignments(self):
        async def collect(database, account_id, year, **kwargs):
            item = {"courseId": "c", "classId": "class", "workId": "new-work", "courseName": "计算机科学", "title": "新增作业", "academicYear": year, "status": "unfinished", "submittable": True, "taskUrl": "https://example.test/work", "deadlineText": "11-30 23:59"}
            item["id"] = database.upsert_assignment(account_id, "chaoxing", item, self.settings, run_id=kwargs["run_id"])
            return {"assignments": [item], "total_courses": 1, "processed_courses": 1, "failed_courses": 0, "failed_course_ids": [], "errors": []}
        platform = SimpleNamespace(collect_assignments=collect)
        sender = AsyncMock(return_value={"weixin": True})
        with patch.object(main, "database", self.db), patch.object(main, "effective_settings", return_value=self.settings), patch.object(main.platform_registry, "get", return_value=platform), patch.object(main, "send_completion_notification", sender):
            first = await main.perform_collection(self.account["id"], 2026, scheduled=True)
            self.assertEqual(first["notification"]["status"], "sent")
            self.assertEqual(first["submittable"], 1)
            second = await main.perform_collection(self.account["id"], 2026, scheduled=True)
            self.assertEqual(second["notification"]["new_count"], 0)
            self.assertEqual(sender.await_count, 1)
            payload = sender.call_args.args[1]
            self.assertEqual(payload["assignments"][0]["course_name"], "计算机科学")
            self.assertEqual(payload["assignments"][0]["title"], "新增作业")

    def test_new_assignment_message_contains_account_course_and_deadline(self):
        text = _weixin_text({"event": "assignments_discovered", "account": "one", "assignments": [self.item()]})
        for value in ("新作业", "one", "计算机科学", "11-30 23:59"):
            self.assertIn(value, text)
