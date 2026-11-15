from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app import main
from app.config import Settings
from app.db import Database
from app.services.assignments.automation_groups import (
    create_group,
    record_discovery,
)
from app.services.assignments.readiness import assess_questions


class AssignmentAutomationGroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(
            data_root=Path(self.temporary.name),
            openai_api_key="fixture",
        )
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        self.account = self.database.create_account("fixture", "chaoxing", self.settings)
        self.database.update_account(self.account["id"], status="ready")
        self.overrides = [
            patch.object(main, "database", self.database),
            patch.object(main, "effective_settings", return_value=self.settings),
        ]
        for override in self.overrides:
            override.start()

    async def asyncTearDown(self):
        for override in reversed(self.overrides):
            override.stop()
        self.temporary.cleanup()

    def assignment(self, title, assessment, *, course="fixture-course"):
        identifier = self.database.upsert_assignment(
            self.account["id"],
            "chaoxing",
            {
                "academicYear": 2026,
                "semester": "2026-2027-1",
                "courseName": course,
                "courseId": course,
                "classId": "class",
                "workId": title,
                "title": title,
                "status": "unfinished",
                "submittable": True,
                "taskUrl": "https://fixture.test/work/" + title,
                "assessment": assessment,
            },
            self.settings,
        )
        return self.database.get_assignment(identifier)

    def group(self, **changes):
        values = {
            "name": "fixture group",
            "account_ids": [self.account["id"]],
            "course_filters": [],
            "windows": [{"days": [1, 2, 3, 4, 5, 6, 7], "start": "00:00", "end": "23:59"}],
            "auto_submit": True,
            "enabled": True,
        }
        values.update(changes)
        return create_group(self.database, values)

    async def test_group_api_supports_crud_and_course_union(self):
        self.assignment("choice", assess_questions({
            "question_count": 1,
            "units": [{"unit_id": "q1", "question_no": 1, "kind": "single", "text": "选择", "options": [{"key": "A"}], "filled": False}],
        }))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            created = await client.post("/api/assignment-auto-groups", json={
                "name": "计算机科学",
                "account_ids": [self.account["id"]],
                "course_filters": ["fixture-course"],
                "windows": [{"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "18:00"}],
                "auto_submit": True,
            })
            self.assertEqual(created.status_code, 200, created.text)
            identifier = created.json()["id"]
            courses = await client.post("/api/assignment-auto-groups/courses", json={"account_ids": [self.account["id"]]})
            self.assertEqual(courses.status_code, 200, courses.text)
            self.assertEqual(courses.json()["courses"][0]["name"], "fixture-course")
            updated = await client.patch(
                f"/api/assignment-auto-groups/{identifier}",
                json={"enabled": False, "name": "计算机科学 2"},
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertFalse(updated.json()["enabled"])
            self.assertEqual((await client.get("/api/assignment-auto-groups")).json()[0]["name"], "计算机科学 2")
            deleted = await client.delete(f"/api/assignment-auto-groups/{identifier}")
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertFalse((await client.get("/api/assignment-auto-groups")).json())

    async def test_first_complete_scan_baselines_and_new_direct_assignment_queues(self):
        group = self.group()
        old = self.assignment("old", assess_questions({
            "question_count": 1,
            "units": [{"unit_id": "q1", "question_no": 1, "kind": "multiple", "text": "选择", "options": [{"key": "A"}], "filled": False}],
        }))
        record_discovery(self.database, group, [old], complete=True)
        self.assertFalse(main.pending_assignment_auto_group_items(self.database))

        newer = self.assignment("new", assess_questions({
            "question_count": 1,
            "units": [{"unit_id": "q1", "question_no": 1, "kind": "fill", "text": "填空", "fields": [{"index": 0}], "filled": False}],
        }))
        material = self.assignment("material", assess_questions({
            "question_count": 1,
            "units": [{"unit_id": "q1", "question_no": 1, "kind": "long", "text": "提交实验报告和压缩包", "fields": [{"index": 0}], "filled": False, "file_inputs": 1}],
        }))
        record_discovery(self.database, group, [newer, material], complete=True)
        pending = main.pending_assignment_auto_group_items(self.database)
        self.assertEqual([item["assignment_id"] for item in pending], [newer["id"]])

    async def test_dispatch_uses_the_group_auto_submit_value(self):
        group = self.group(auto_submit=True)
        old = self.assignment("old", assess_questions({
            "question_count": 1,
            "units": [{"unit_id": "q1", "question_no": 1, "kind": "single", "text": "选择", "options": [{"key": "A"}], "filled": False}],
        }))
        record_discovery(self.database, group, [old], complete=True)
        new = self.assignment("new", assess_questions({
            "question_count": 1,
            "units": [{"unit_id": "q1", "question_no": 1, "kind": "single", "text": "选择", "options": [{"key": "A"}], "filled": False}],
        }))
        record_discovery(self.database, group, [new], complete=True)
        fake_job = {"id": "job-one", "assignment_id": new["id"], "trigger": "auto_group", "status": "queued", "auto_submit": 1}
        with patch.object(main, "enqueue_assignment", AsyncMock(return_value=fake_job)) as enqueue:
            await main.dispatch_assignment_group_items()
        call = enqueue.await_args
        self.assertEqual(call.args[0].assignment_id, new["id"])
        self.assertTrue(call.args[0].auto_submit)
        self.assertEqual(call.kwargs["trigger"], "auto_group")
        self.assertEqual(main.pending_assignment_auto_group_items(self.database), [])

    async def test_manual_scan_records_completion_and_dispatches_group_queue(self):
        group = self.group()
        with patch.object(
            main,
            "perform_collection",
            AsyncMock(return_value={"status": "success"}),
        ), patch.object(
            main,
            "dispatch_assignment_group_items",
            AsyncMock(),
        ) as dispatch, patch.object(
            main.preview_manager,
            "is_interactive",
            AsyncMock(return_value=False),
        ), patch.object(
            main,
            "learning_service",
            SimpleNamespace(active=lambda _: False),
        ):
            result = await main.scan_assignment_auto_group(group["id"], manual=True)
        self.assertEqual(result["last_status"], "completed")
        self.assertTrue(result["baseline_ready"])
        dispatch.assert_awaited_once()

    async def test_scan_window_and_forced_review_defaults(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from app.services.assignments.automation_groups import window_open

        china = ZoneInfo("Asia/Shanghai")
        self.assertTrue(window_open([{"days": [1, 2, 3, 4, 5, 6, 7], "start": "00:00", "end": "23:59"}], datetime(2026, 11, 16, 12, 0, tzinfo=china)))
        self.assertTrue(window_open([{"days": [1, 2, 3, 4, 5, 6, 7], "start": "22:00", "end": "02:00"}], datetime(2026, 11, 16, 23, 0, tzinfo=china)))
        from app.preferences import load_preferences
        self.database.set_setting("preferences", {
            "assignments": {"auto_submit": False, "educoder_auto_evaluate": False},
            "learning": {"auto_submit_quiz": False},
        })
        preferences = load_preferences(self.database)
        self.assertTrue(preferences.assignments.educoder_auto_evaluate)
        self.assertTrue(preferences.learning.auto_submit_quiz)
