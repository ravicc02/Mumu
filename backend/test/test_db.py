from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.services.notifier import _weixin_text


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp_dir.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_account_and_assignment_round_trip(self) -> None:
        account = self.database.create_account("测试用户", "chaoxing", self.settings)
        assignment_id = self.database.upsert_assignment(
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
                "deadlineText": "11-01 至 11-30",
                "taskUrl": "https://example.test/job/1",
                "workId": "work-1",
            },
            self.settings,
        )

        assignments = self.database.list_assignments(
            account_id=account["id"],
            academic_year=2026,
            submittable_only=True,
        )
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["id"], assignment_id)
        self.assertEqual(assignments[0]["semester"], "2026-2027-1")

    def test_job_and_evidence_round_trip(self) -> None:
        account = self.database.create_account("测试用户", "chaoxing", self.settings)
        assignment_id = self.database.upsert_assignment(
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
                "taskUrl": "https://example.test/job/1",
                "workId": "work-1",
            },
            self.settings,
        )
        job = self.database.create_job(account["id"], assignment_id, False)
        self.assertTrue(self.database.has_active_job(account["id"]))
        self.database.update_job(job["id"], status="completed", progress=100)
        self.assertFalse(self.database.has_active_job(account["id"]))
        self.database.add_event(job["id"], "completed", "done")
        artifact = self.database.add_artifact(
            job["id"],
            "screenshot",
            Path(self.temp_dir.name) / "screen.png",
        )

        self.assertEqual(self.database.get_job(job["id"])["status"], "completed")
        self.assertEqual(self.database.list_events(job["id"])[0]["kind"], "completed")
        self.assertEqual(
            self.database.list_artifacts(job["id"])[0]["id"],
            artifact["id"],
        )

    def test_weixin_completion_text_contains_core_fields(self) -> None:
        text = _weixin_text(
            {
                "account": "测试用户",
                "course": "软件工程",
                "assignment": "实验一",
                "auto_submit": False,
                "result": "已完成填写",
            }
        )
        self.assertIn("测试用户", text)
        self.assertIn("软件工程", text)
        self.assertIn("自动提交：否", text)


if __name__ == "__main__":
    unittest.main()
