from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from app import main
from app.config import Settings
from app.db import Database, ActiveJobConflict
from app.preferences import AccountSchedule, PreferencesUpdate, load_preferences
from app.services.assignment_automation import save_preferences, record_discovery, pending_assignments, automation_status
from app.services.notifier import _weixin_text, notification_examples
from app.services.learning.browser import LearningBrowser


class PreferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_browser_toggle_is_normalized_to_the_enabled_default(self):
        self.database.set_setting("preferences", {"assignments": {"auto_submit": False, "interactive": False}})
        preferences = load_preferences(self.database)
        self.assertTrue(preferences.assignments.interactive)
        self.assertFalse(preferences.assignments.auto_submit)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        self.account = self.database.create_account("fixture", "chaoxing", self.settings)
        self.database.update_account(self.account["id"], status="ready")
        self.database.set_setting("model_config", {"api_key": "fixture", "models": ["fixture-model"], "base_url": "https://fixture.test/v1"})
        self.overrides = [patch.object(main, "database", self.database),
                          patch.object(main, "learning_service", SimpleNamespace(active=lambda _: None)),
                          patch.object(main, "platform_login_in_progress", return_value=False),
                          patch.object(main.preview_manager, "is_interactive", AsyncMock(return_value=False)),
                          patch.object(main, "start_job", Mock())]
        for override in self.overrides: override.start()

    def tearDown(self):
        for override in reversed(self.overrides): override.stop()
        self.temp.cleanup()

    def assignment(self, name, account=None, submittable=True):
        account = account or self.account
        identifier = self.database.upsert_assignment(account["id"], "chaoxing", {
            "academicYear": 2026, "courseName": "fixture-course", "courseId": "one", "classId": "one",
            "workId": name, "title": name, "status": "unfinished", "submittable": submittable,
            "taskUrl": "https://fixture.test/work/" + name,
        }, self.settings)
        return self.database.get_assignment(identifier)

    def enable(self, account=None, **changes):
        config = dict(account_id=(account or self.account)["id"], enabled=True, auto_execute=True, interval_value=5, interval_unit="s")
        config.update(changes)
        save_preferences(self.database, PreferencesUpdate(schedules=[AccountSchedule(**config)]))

    def test_interval_units_are_canonical_and_legacy_due_still_works(self):
        for value, unit, seconds in [(5, "s", 5), (1.5, "min", 90), (2, "h", 7200)]:
            self.enable(interval_value=value, interval_unit=unit)
            account = self.database.get_account(self.account["id"])
            self.assertEqual(account["auto_collect_interval_seconds"], seconds)
            self.database.update_account(account["id"], last_collection_at=(datetime.now(timezone.utc) - timedelta(seconds=seconds + 1)).isoformat())
            self.assertTrue(main.collection_due(self.database.get_account(account["id"])))
        with self.assertRaises(ValidationError): self.enable(interval_value=4)
        with self.assertRaises(ValidationError): self.enable(interval_value=200, interval_unit="h")
        self.assertTrue(main.collection_due({"auto_collect_enabled": 1, "auto_collect_interval_minutes": 30, "last_collection_at": (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()}))

    def test_existing_assignments_are_baselined_and_only_new_items_execute_once(self):
        old = self.assignment("old")
        self.enable()
        new = self.assignment("new")
        record_discovery(self.database, self.account["id"], [old, new], scheduled=True)
        record_discovery(self.database, self.account["id"], [old, new], scheduled=True)
        self.assertEqual([item["assignment_id"] for item in pending_assignments(self.database)], [new["id"]])
        restarted = Database(self.settings); restarted.initialize()
        self.assertEqual(len(pending_assignments(restarted)), 1)

    def test_first_successful_snapshot_does_not_execute_an_unknown_backlog(self):
        self.enable()
        old = self.assignment("old")
        record_discovery(self.database, self.account["id"], [old], scheduled=True, complete=False)
        self.assertEqual(self.database.get_account(self.account["id"])["automation_baseline_ready"], 0)
        second = self.assignment("second")
        record_discovery(self.database, self.account["id"], [old, second], scheduled=True)
        self.assertFalse(pending_assignments(self.database))
        third = self.assignment("third")
        record_discovery(self.database, self.account["id"], [third], scheduled=True)
        self.assertEqual(pending_assignments(self.database)[0]["assignment_id"], third["id"])

    async def test_dispatch_uses_execution_defaults_and_never_retries_after_restart(self):
        self.assignment("old"); self.enable()
        new = self.assignment("new")
        record_discovery(self.database, self.account["id"], [new], scheduled=True)
        save_preferences(self.database, PreferencesUpdate(assignments={"auto_submit": True, "interactive": False, "max_steps": 60, "concurrency": 2}))
        await main.dispatch_automatic_assignments()
        jobs = self.database.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0]["auto_submit"], jobs[0]["interactive"], jobs[0]["trigger"]), (1, 1, "scheduled"))
        self.assertEqual(main.effective_settings().agent_max_steps, 60)
        self.database.transition_job(jobs[0]["id"], "interrupted")
        record_discovery(self.database, self.account["id"], [new], scheduled=True)
        await main.dispatch_automatic_assignments()
        self.assertEqual(len(self.database.list_jobs()), 1)
        self.assertFalse(pending_assignments(self.database))

    async def test_manual_review_does_not_block_another_assignment_and_missing_model_still_waits(self):
        old = self.assignment("old"); self.enable()
        new = self.assignment("new")
        record_discovery(self.database, self.account["id"], [new], scheduled=True)
        self.database.create_job(self.account["id"], old["id"], False)
        with patch.object(main, "job_browser_state", side_effect=lambda job: {**job, "browser_retained": True}):
            await main.dispatch_automatic_assignments()
        self.assertEqual(len(self.database.list_jobs()), 2)
        self.assertFalse(pending_assignments(self.database))
        another = self.assignment("another")
        record_discovery(self.database, self.account["id"], [another], scheduled=True)
        self.database.set_setting("model_config", {"api_key": "", "models": ["fixture"]})
        await main.dispatch_automatic_assignments()
        self.assertIn("配置模型", automation_status(self.database, self.account["id"])["reason"])

    async def test_disabling_cancels_pending_work_but_preserves_manual_jobs(self):
        self.assignment("old"); self.enable()
        new = self.assignment("new")
        record_discovery(self.database, self.account["id"], [new], scheduled=True)
        manual = self.database.create_job(self.account["id"], self.assignment("manual")["id"], False)
        self.enable(enabled=False, auto_execute=False)
        await main.dispatch_automatic_assignments()
        self.assertFalse(pending_assignments(self.database))
        self.assertEqual(self.database.get_job(manual["id"])["status"], "queued")

    def test_invalid_account_rolls_back_preferences_and_all_schedule_changes(self):
        update = PreferencesUpdate(assignments={"max_steps": 80}, schedules=[
            AccountSchedule(account_id=self.account["id"], enabled=True, interval_value=30, interval_unit="s"),
            AccountSchedule(account_id="missing", enabled=True, interval_value=30, interval_unit="s")])
        with self.assertRaises(ValueError): save_preferences(self.database, update)
        self.assertFalse(self.database.get_account(self.account["id"])["auto_collect_enabled"])
        self.assertEqual(load_preferences(self.database).assignments.max_steps, 100)

    def test_expired_accounts_can_keep_or_disable_existing_schedules(self):
        self.enable()
        self.database.update_account(self.account["id"], status="expired")
        self.enable(interval_value=10)
        self.assertTrue(self.database.get_account(self.account["id"])["auto_collect_enabled"])
        self.enable(enabled=False, auto_execute=False)
        self.assertFalse(self.database.get_account(self.account["id"])["auto_collect_enabled"])
        with self.assertRaisesRegex(ValueError, "尚未授权"): self.enable()

    def test_automatic_job_claim_is_atomic_and_cannot_be_created_twice(self):
        self.assignment("old"); self.enable()
        item = self.assignment("new")
        record_discovery(self.database, self.account["id"], [item], scheduled=True)
        self.database.create_job(self.account["id"], item["id"], False, trigger="scheduled")
        with self.assertRaises(ActiveJobConflict): self.database.create_job(self.account["id"], item["id"], False, trigger="scheduled")
        self.assertEqual(len(self.database.list_jobs()), 1)

    async def test_legacy_minutes_endpoint_updates_canonical_interval(self):
        result = await main.update_collection_schedule(self.account["id"], main.CollectionScheduleRequest(enabled=True, interval_minutes=45))
        self.assertEqual(result["auto_collect_interval_seconds"], 2700)
        self.assertEqual(result["auto_collect_interval_unit"], "min")
        self.assertFalse(result["auto_execute_enabled"])

    async def test_collection_pipeline_builds_baseline_then_dispatches_new_assignments(self):
        self.enable()
        titles = ["old"]
        async def collect(database, account_id, year, **kwargs):
            assignments = []
            for title in titles:
                item = {"courseId": "c", "classId": "class", "workId": title, "courseName": "fixture-course",
                        "title": title, "academicYear": year, "status": "unfinished", "submittable": True,
                        "taskUrl": "https://fixture.test/work/" + title}
                item["id"] = database.upsert_assignment(account_id, "chaoxing", item, self.settings, run_id=kwargs["run_id"])
                assignments.append(item)
            return {"assignments": assignments, "total_courses": 1, "processed_courses": 1,
                    "failed_courses": 0, "failed_course_ids": [], "errors": []}
        with patch.object(main.platform_registry, "get", return_value=SimpleNamespace(collect_assignments=collect)), patch.object(main, "send_completion_notification", AsyncMock()):
            first = await main.perform_collection(self.account["id"], 2026, scheduled=True)
            self.assertEqual(first["status"], "success")
            self.assertEqual(self.database.get_account(self.account["id"])["automation_baseline_ready"], 1)
            self.assertFalse(pending_assignments(self.database))
            titles.append("new")
            await main.perform_collection(self.account["id"], 2026, scheduled=True)
            await main.dispatch_automatic_assignments()
            jobs = self.database.list_jobs()
            self.assertEqual([job["assignment_title"] for job in jobs], ["new"])
            self.assertEqual(jobs[0]["auto_submit"], 0)
            self.assertEqual(jobs[0]["interactive"], 1)

    async def test_preferences_api_rejects_invalid_input_without_partial_write(self):
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            invalid = await client.patch("/api/preferences", json={"system": {"log_retention_days": 0}})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual((await client.get("/api/preferences")).json()["system"]["log_retention_days"], 14)
            valid = await client.patch("/api/preferences", json={"learning": {"playback_rate": 1.5, "volume": 40}})
            self.assertEqual(valid.status_code, 200)
            self.assertEqual((await client.get("/api/preferences")).json()["learning"]["playback_rate"], 1.5)

    async def test_disabling_only_stops_queued_jobs_owned_by_automation(self):
        self.assignment("old"); self.enable()
        new = self.assignment("new")
        record_discovery(self.database, self.account["id"], [new], scheduled=True)
        automated = self.database.create_job(self.account["id"], new["id"], False, trigger="scheduled")
        self.database.create_job(self.account["id"], self.assignment("manual")["id"], False)
        with patch.object(main, "stop_job") as stop:
            await main.update_preferences(PreferencesUpdate(schedules=[AccountSchedule(account_id=self.account["id"], enabled=False,
                auto_execute=False, interval_value=30, interval_unit="s")]))
            stop.assert_called_once_with(self.database, main.event_hub, automated["id"])

    async def test_scheduler_checks_each_second_instead_of_once_per_minute(self):
        with patch.object(main, "dispatch_automatic_assignments", AsyncMock()) as dispatch, patch.object(main, "schedule_due_collections", AsyncMock()), patch.object(main.asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])) as sleep:
            with self.assertRaises(asyncio.CancelledError): await main.run_collection_scheduler()
            self.assertEqual(sleep.call_args_list[1].args, (1,))
            dispatch.assert_awaited_once()

    async def test_learning_play_options_are_forwarded_to_native_player(self):
        browser = LearningBrowser.__new__(LearningBrowser)
        browser.io_lock = asyncio.Lock()
        browser.learning_options = {"playback_rate": 1.5, "volume": 30, "resource_wait_seconds": 40}
        browser.call = AsyncMock(side_effect=[{"challenge": False, "quiz": False, "error": ""}, {"started": True}])
        await browser.play("frame")
        browser.call.assert_any_await("frame", "play", browser.learning_options)

    def test_retention_configuration_applies_to_every_prune(self):
        save_preferences(self.database, PreferencesUpdate(system={"log_retention_days": 30, "log_size_mb": 400}))
        with patch.object(self.database.diagnostics, "_prune") as prune:
            self.database.diagnostics.prune()
            prune.assert_called_once_with(days=30, total_bytes=400 * 1024 * 1024, protected=())

    def test_notifications_are_short_and_do_not_expose_internal_urls(self):
        payload = {"event": "assignment_incomplete", "account": "fixture", "course": "course", "assignment": "work",
                   "result": "页面填写与核验失败 https://fixture.test/?private=secret " + "details " * 100}
        text = _weixin_text(payload)
        self.assertNotIn("secret", text)
        self.assertNotIn("http", text)
        self.assertLess(len(text), 120)
        self.assertEqual(len(text.splitlines()), 3)
        for example in notification_examples().values(): self.assertLessEqual(len(example.splitlines()), 3)

    async def test_settings_and_connector_card_share_honest_delivery_readiness(self):
        self.database.set_setting("weixin_binding", {"token": "fixture", "user_id": "fixture", "context_token": "fixture"})
        self.database.set_setting("weixin_delivery", {"error": "provider refused delivery"})
        self.assertFalse((await main.get_settings())["weixin_ready"])
        self.assertFalse((await main.get_weixin_connector())["ready"])
        self.database.set_setting("weixin_delivery", {})
        self.assertTrue((await main.get_settings())["weixin_ready"])
        self.assertTrue((await main.get_weixin_connector())["ready"])
