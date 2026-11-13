from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings
from app.db import ActiveJobConflict, Database
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
        self.assertEqual(account["auto_collect_enabled"], 0)
        self.assertEqual(account["auto_collect_interval_minutes"], 360)
        self.database.update_account(
            account["id"],
            auto_collect_enabled=1,
            auto_collect_interval_minutes=60,
        )
        self.assertEqual(
            self.database.get_account(account["id"])["auto_collect_enabled"],
            1,
        )
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
        self.assertEqual(job["attempt"], 1)
        self.assertEqual(job["submission_status"], "not_started")
        self.assertTrue(job["updated_at"])
        self.assertTrue(self.database.has_active_job(account["id"]))
        self.assertTrue(
            self.database.has_active_job_for_assignment(assignment_id)
        )
        with self.assertRaises(ActiveJobConflict):
            self.database.create_job(account["id"], assignment_id, False)
        with self.assertRaises(ValueError):
            self.database.update_job(job["id"], status="completed", progress=100)
        self.database.transition_job(job["id"], "running")
        self.database.update_job(job["id"], status="completed", submission_status="submitted_verified", progress=100)
        self.assertFalse(self.database.has_active_job(account["id"]))
        self.assertFalse(
            self.database.has_active_job_for_assignment(assignment_id)
        )
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
                "event": "assignment_failed",
                "account": "测试用户",
                "course": "软件工程",
                "assignment": "实验一",
                "auto_submit": False,
                "submission_status": "not_submitted",
                "result": "浏览器连接失败",
            }
        )
        self.assertIn("中断", text)
        self.assertIn("测试用户", text)
        self.assertIn("软件工程", text)
        self.assertIn("浏览器连接失败", text)
        self.assertEqual(len(text.splitlines()), 3)
        self.assertNotIn("自动提交", text)

    def test_state_transition_validation_and_interruption_event(self) -> None:
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
        self.database.transition_job(job["id"], "running")
        self.database.transition_job(job["id"], "paused")
        self.database.transition_job(job["id"], "running")
        with self.assertRaises(ValueError):
            self.database.transition_job(job["id"], "queued")

        self.database.transition_job(job["id"], "stopping")
        self.database.transition_job(job["id"], "stopped")
        with self.assertRaises(ValueError):
            self.database.transition_job(job["id"], "running")

        interrupted = self.database.create_job(
            account["id"],
            assignment_id,
            False,
        )
        self.database.mark_interrupted_jobs()
        self.assertEqual(
            self.database.get_job(interrupted["id"])["status"],
            "interrupted",
        )
        events = self.database.list_events(interrupted["id"])
        self.assertEqual(events[-1]["kind"], "interrupted")

        self.database.update_account(account["id"], status="authorizing")
        self.database.initialize()
        self.assertEqual(
            self.database.get_account(account["id"])["status"],
            "login_error",
        )

    def test_collection_snapshot_keeps_failed_courses_and_hides_stale_rows(self) -> None:
        account = self.database.create_account("测试用户", "chaoxing", self.settings)
        first_id = self.database.upsert_assignment(
            account["id"],
            "chaoxing",
            {
                "academicYear": 2026,
                "courseName": "课程一",
                "courseId": "course-1",
                "classId": "class-1",
                "title": "作业一",
                "status": "unfinished",
                "submittable": True,
                "taskUrl": "https://example.test/1",
                "workId": "work-1",
            },
            self.settings,
        )
        second_id = self.database.upsert_assignment(
            account["id"],
            "chaoxing",
            {
                "academicYear": 2026,
                "courseName": "课程二",
                "courseId": "course-2",
                "classId": "class-2",
                "title": "作业二",
                "status": "unfinished",
                "submittable": True,
                "taskUrl": "https://example.test/2",
                "workId": "work-2",
            },
            self.settings,
        )
        run = self.database.create_collection_run(account["id"], 2026)
        self.assertEqual(run["status"], "running")
        self.database.upsert_assignment(
            account["id"],
            "chaoxing",
            {
                "academicYear": 2026,
                "courseName": "课程一",
                "courseId": "course-1",
                "classId": "class-1",
                "title": "作业一",
                "status": "unfinished",
                "submittable": True,
                "taskUrl": "https://example.test/1",
                "workId": "work-1",
            },
            self.settings,
            run_id=run["id"],
        )
        self.database.finalize_assignment_snapshot(
            account["id"],
            2026,
            run["id"],
            {"course-2"},
        )
        active_ids = {
            item["id"]
            for item in self.database.list_assignments(
                account_id=account["id"],
                academic_year=2026,
            )
        }
        self.assertEqual(active_ids, {first_id, second_id})

        finished = self.database.finish_collection_run(
            run["id"],
            "partial",
            total_courses=2,
            processed_courses=1,
            failed_courses=1,
            assignment_count=1,
            submittable_count=1,
            error="1 个课程采集失败",
            errors=[{"course_id": "course-2", "error": "timeout"}],
        )
        self.assertEqual(finished["status"], "partial")
        self.assertEqual(finished["errors"][0]["course_id"], "course-2")
        self.assertEqual(
            self.database.get_account(account["id"])["last_collection_status"],
            "partial",
        )

        clean_run = self.database.create_collection_run(account["id"], 2026)
        self.database.upsert_assignment(
            account["id"],
            "chaoxing",
            {
                "academicYear": 2026,
                "courseName": "课程一",
                "courseId": "course-1",
                "classId": "class-1",
                "title": "作业一",
                "status": "unfinished",
                "submittable": True,
                "taskUrl": "https://example.test/1",
                "workId": "work-1",
            },
            self.settings,
            run_id=clean_run["id"],
        )
        self.database.finalize_assignment_snapshot(
            account["id"],
            2026,
            clean_run["id"],
        )
        active_ids = {
            item["id"]
            for item in self.database.list_assignments(
                account_id=account["id"],
                academic_year=2026,
            )
        }
        self.assertEqual(active_ids, {first_id})

    def test_job_attempts_and_manual_submission_verification(self) -> None:
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
                "taskUrl": "https://example.test/1",
                "workId": "work-1",
            },
            self.settings,
        )
        first = self.database.create_job(account["id"], assignment_id, True)
        self.database.transition_job(
            first["id"],
            "running",
            started_at="2026-01-01T00:00:00+00:00",
        )
        with self.assertRaises(ValueError):
            self.database.transition_job(first["id"], "completed", submission_status="submitted_unverified")
        self.database.transition_job(
            first["id"],
            "completed",
            submission_status="submitted_verified",
        )
        parent_id, attempt = self.database.next_attempt_for_assignment(assignment_id)
        self.assertEqual(parent_id, first["id"])
        self.assertEqual(attempt, 2)
        second = self.database.create_job(
            account["id"],
            assignment_id,
            False,
            parent_job_id=parent_id,
            attempt=attempt,
        )
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(second["parent_job_id"], first["id"])
        with self.assertRaises(RuntimeError):
            self.database.update_submission_status(
                second["id"],
                "submitted_verified",
            )
        self.database.transition_job(
            second["id"],
            "failed",
            error="test",
        )
        verified = self.database.update_submission_status(
            second["id"],
            "submitted_verified",
            "已人工核对",
        )
        self.assertEqual(verified["submission_status"], "submitted_verified")
        self.assertEqual(verified["status"], "completed")
        self.assertEqual(verified["verification_note"], "已人工核对")
        self.assertTrue(verified["verified_at"])

    def test_stale_running_job_is_reported_once(self) -> None:
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
                "taskUrl": "https://example.test/1",
                "workId": "work-1",
            },
            self.settings,
        )
        job = self.database.create_job(account["id"], assignment_id, False)
        self.database.transition_job(job["id"], "running")
        old_heartbeat = (
            datetime.now(timezone.utc) - timedelta(seconds=200)
        ).isoformat()
        self.database.update_job(
            job["id"],
            last_heartbeat_at=old_heartbeat,
        )
        stale = self.database.stale_running_jobs(120)
        self.assertEqual([item["id"] for item in stale], [job["id"]])
        self.database.add_event(job["id"], "stalled", "test")
        self.assertEqual(self.database.stale_running_jobs(120), [])

    def test_needs_intervention_keeps_assignment_locked(self) -> None:
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
                "taskUrl": "https://example.test/1",
                "workId": "work-1",
            },
            self.settings,
        )
        job = self.database.create_job(account["id"], assignment_id, False)
        self.database.transition_job(job["id"], "running")
        self.database.transition_job(
            job["id"],
            "needs_intervention",
            submission_status="filled_pending_review",
            browser_retained=1,
        )
        self.assertTrue(self.database.has_active_job(account["id"]))
        with self.assertRaises(ActiveJobConflict):
            self.database.create_job(account["id"], assignment_id, False)
        reviewed = self.database.update_submission_status(job["id"], "filled_verified")
        self.assertEqual(reviewed["status"], "needs_intervention")
        self.database.update_job(job["id"], browser_retained=0)
        self.assertFalse(self.database.has_active_job(account["id"]))

    def test_running_collection_is_failed_after_restart(self) -> None:
        account = self.database.create_account("测试用户", "chaoxing", self.settings)
        run = self.database.create_collection_run(account["id"], 2026)
        self.database.update_account(
            account["id"],
            last_collection_status="running",
            last_collection_run_id=run["id"],
        )

        self.database.initialize()

        interrupted = self.database.get_collection_run(run["id"])
        self.assertEqual(interrupted["status"], "failed")
        self.assertEqual(interrupted["error"], "应用重启，采集已中断")
        self.assertEqual(
            self.database.get_account(account["id"])["last_collection_status"],
            "error",
        )


if __name__ == "__main__":
    unittest.main()
