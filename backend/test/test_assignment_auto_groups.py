from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db import Database
from app.services.assignments.automation_groups import (
    ALL_DAY_WINDOW,
    course_catalog,
    create_group,
    delete_group,
    get_group,
    list_groups,
    mark_item,
    mark_scan_finished,
    mark_scan_started,
    pending_items,
    record_discovery,
    scan_due,
    update_group,
    window_open,
)


class AssignmentAutoGroupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        self.account = self.database.create_account(
            "Fixture",
            "chaoxing",
            self.settings,
        )
        self.database.update_account(self.account["id"], status="ready")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assignment(
        self,
        name: str,
        *,
        course: str = "Course",
        execution_state: str = "direct_fill",
    ) -> dict:
        identifier = self.database.upsert_assignment(
            self.account["id"],
            "chaoxing",
            {
                "academicYear": 2026,
                "courseName": course,
                "courseId": f"course-{name}",
                "classId": f"class-{name}",
                "workId": f"work-{name}",
                "title": name,
                "status": "unfinished",
                "submittable": True,
                "taskUrl": f"https://example.test/work/{name}",
            },
            self.settings,
        )
        self.database.update_assignment_assessment(
            identifier,
            {
                "execution_state": execution_state,
                "policy_version": 3,
                "automation_supported": execution_state == "direct_fill",
                "requirements": [],
                "supported_kinds": ["fill", "multiple", "single"],
            },
        )
        return self.database.get_assignment(identifier)

    def group(self) -> dict:
        return create_group(
            self.database,
            {
                "name": "Daily",
                "account_ids": [self.account["id"]],
                "course_filters": [],
                "windows": [ALL_DAY_WINDOW],
                "auto_submit": False,
                "enabled": True,
            },
        )

    def test_crud_course_catalog_and_scope_reset(self):
        first = self.assignment("One", course="Algorithms")
        second = self.assignment("Two", course="Algorithms")
        self.database.update_assignment_assessment(first["id"], {
            "execution_state": "direct_fill",
            "policy_version": 3,
            "automation_supported": True,
        })
        group = self.group()
        catalog = course_catalog(self.database, [self.account["id"]])

        self.assertEqual(len(list_groups(self.database)), 1)
        self.assertEqual(catalog["courses"][0]["name"], "Algorithms")
        self.assertTrue(catalog["courses"][0]["common"])
        record_discovery(
            self.database,
            group,
            [first, second],
            complete=True,
        )
        self.assertTrue(get_group(self.database, group["id"])["baseline_ready"])

        updated = update_group(
            self.database,
            group["id"],
            {
                "name": "Updated",
                "course_filters": ["Algorithms"],
                "auto_submit": True,
            },
        )
        self.assertEqual(updated["name"], "Updated")
        self.assertTrue(updated["auto_submit"])
        self.assertFalse(updated["baseline_ready"])

        delete_group(self.database, group["id"])
        self.assertEqual(list_groups(self.database), [])

    def test_baseline_queues_only_new_direct_answerable_items(self):
        existing = self.assignment("Existing")
        group = self.group()
        record_discovery(self.database, group, [existing], complete=True)
        self.assertEqual(pending_items(self.database), [])

        direct = self.assignment("Direct")
        manual = self.assignment("Manual", execution_state="manual_required")
        refreshed = get_group(self.database, group["id"])
        record_discovery(
            self.database,
            refreshed,
            [existing, direct, manual],
            complete=True,
        )
        pending = pending_items(self.database)
        self.assertEqual([item["assignment_id"] for item in pending], [direct["id"]])

        with self.database.connect() as connection:
            manual_state = connection.execute(
                """
                SELECT state FROM assignment_auto_group_items
                WHERE group_id = ? AND assignment_id = ?
                """,
                (group["id"], manual["id"]),
            ).fetchone()["state"]
        self.assertEqual(manual_state, "skipped")

    def test_scan_window_and_status_transitions(self):
        group = self.group()
        mark_scan_started(self.database, group["id"])
        running = get_group(self.database, group["id"])
        self.assertEqual(running["last_status"], "running")
        self.assertFalse(scan_due(running))

        mark_scan_finished(
            self.database,
            group["id"],
            status="completed",
            error="",
        )
        self.assertEqual(
            get_group(self.database, group["id"])["last_status"],
            "completed",
        )
        self.assertTrue(window_open(
            [{"days": [1], "start": "08:00", "end": "10:00"}],
            datetime(2026, 11, 16, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ))
        self.assertFalse(window_open(
            [{"days": [1], "start": "08:00", "end": "10:00"}],
            datetime(2026, 11, 16, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ))


if __name__ == "__main__":
    unittest.main()
