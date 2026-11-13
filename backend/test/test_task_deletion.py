from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.db import ActiveJobConflict, Database
from app.events import EventHub
from app.services.job_state import available_job_actions
from app.services.learning.routes import learning_router
from app.services.learning.service import LearningService


class TaskDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.root.name))
        self.settings.ensure_directories()
        self.db = Database(self.settings)
        self.db.initialize()
        self.account = self.db.create_account("delete-test", "chaoxing", self.settings)
        self.db.update_account(self.account["id"], status="ready")
        self.service = LearningService(self.db, self.settings, EventHub())
        self.service.initialize()
        self.assignment = self.db.upsert_assignment(self.account["id"], "chaoxing", {
            "title": "test assignment", "taskUrl": "https://example.test/work",
            "workId": "one", "courseId": "course", "classId": "class", "submittable": True,
        }, self.settings)
        self.course = self.service.store.upsert_courses(self.account["id"], [{
            "courseId": "course", "classId": "class", "courseName": "test course", "url": "https://example.test/course",
        }], 2026)[0]

    async def asyncTearDown(self):
        await self.service.shutdown()
        self.root.cleanup()

    def job(self, attempt=1):
        return self.db.create_job(self.account["id"], self.assignment, False, attempt=attempt)

    async def test_assignment_deletion_hides_every_attempt_but_keeps_evidence_and_other_accounts(self):
        first = self.job()
        self.db.transition_job(first["id"], "stopped")
        self.db.add_event(first["id"], "test", "kept diagnostic evidence")
        latest = self.job(2)
        self.db.transition_job(latest["id"], "stopped")
        other = self.db.create_account("other", "chaoxing", self.settings)
        other_assignment = self.db.upsert_assignment(other["id"], "chaoxing", {
            "title": "test assignment", "taskUrl": "https://example.test/work",
            "workId": "one", "courseId": "course", "classId": "class", "submittable": True,
        }, self.settings)
        other_job = self.db.create_job(other["id"], other_assignment, False)
        result = self.db.delete_job_task(latest["id"])
        self.assertEqual(set(result["deleted_ids"]), {first["id"], latest["id"]})
        self.assertEqual([job["id"] for job in self.db.list_jobs(include_deleted=False)], [other_job["id"]])
        self.assertTrue(self.db.get_job(first["id"])["deleted_at"])
        self.assertTrue(self.db.list_events(first["id"]))
        self.assertTrue(self.db.get_assignment(self.assignment))
        self.assertEqual(self.db.delete_job_task(first["id"]), result)
        self.db.initialize()
        self.assertEqual(len(self.db.list_jobs(include_deleted=False)), 1)
        next_job = self.job(3)
        self.assertIn(next_job["id"], [job["id"] for job in self.db.list_jobs(include_deleted=False)])

    async def test_assignment_cannot_be_deleted_through_an_old_attempt_while_new_attempt_is_busy(self):
        old = self.job()
        self.db.transition_job(old["id"], "stopped")
        current = self.job(2)
        with self.assertRaises(ActiveJobConflict): self.db.delete_job_task(old["id"])
        self.db.transition_job(current["id"], "running")
        self.db.transition_job(current["id"], "needs_intervention", submission_status="filled_pending_review")
        self.db.update_job(current["id"], browser_retained=1)
        with self.assertRaises(ActiveJobConflict): self.db.delete_job_task(old["id"])
        self.assertFalse(self.db.get_job(old["id"])["deleted_at"])

    async def test_assignment_routes_keep_submission_protection_after_deletion(self):
        from app import main
        job = self.job()
        self.db.transition_job(job["id"], "running")
        self.db.transition_job(job["id"], "completed", submission_status="submitted_verified")
        with patch.object(main, "database", self.db), patch.object(main, "effective_settings", return_value=self.settings), patch.object(main, "start_job") as start:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                response = await client.delete(f"/api/jobs/{job['id']}")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual((await client.get("/api/jobs")).json(), [])
                response = await client.post("/api/jobs", json={"assignment_id": self.assignment})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("完成并提交", response.json()["detail"])
                self.assertEqual((await client.delete("/api/jobs/missing")).status_code, 404)
            start.assert_not_called()

    async def test_learning_deletion_hides_all_attempts_and_allows_a_new_run(self):
        first = self.service.store.create_run(self.course["id"])
        self.service.store.update(first["id"], status="stopped")
        self.service.store.event(first["id"], "test", "kept learning evidence")
        second = self.service.store.create_run(self.course["id"])
        self.service.store.update(second["id"], status="stopped")
        result = self.service.delete_task(second["id"])
        self.assertEqual(set(result["deleted_ids"]), {first["id"], second["id"]})
        self.assertEqual(self.service.store.runs(), [])
        self.assertTrue(self.service.store.events(first["id"]))
        self.service.store.initialize()
        self.assertEqual(self.service.store.runs(), [])
        new = self.service.store.create_run(self.course["id"])
        self.assertEqual(new["attempt"], 3)
        self.assertEqual([run["id"] for run in self.service.store.runs()], [new["id"]])

    async def test_learning_routes_reject_busy_runs_and_preserve_the_course(self):
        app = FastAPI()
        app.include_router(learning_router(self.service, lambda *args: None, lambda *args: None, lambda *args: None))
        run = self.service.store.create_run(self.course["id"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            endpoint = f"/api/learning/runs/{run['id']}"
            self.assertEqual((await client.delete(endpoint)).status_code, 409)
            self.service.store.update(run["id"], status="stopped")
            self.service.browsers[run["id"]] = object()
            try: self.assertEqual((await client.delete(endpoint)).status_code, 409)
            finally: self.service.browsers.pop(run["id"])
            self.assertEqual((await client.delete(endpoint)).status_code, 200)
            self.assertEqual((await client.get("/api/learning/runs")).json(), [])
            courses = (await client.get("/api/learning/courses", params={"account_id": self.account["id"]})).json()
            self.assertEqual(courses[0]["id"], self.course["id"])
            self.assertIsNone(courses[0]["last_run"])
            self.assertEqual((await client.delete("/api/learning/runs/missing")).status_code, 404)

    async def test_delete_action_requires_a_settled_task_without_a_browser(self):
        for status in ["queued", "running", "paused", "stopping"]:
            self.assertNotIn("delete", available_job_actions({"status": status}, controlled=False, browser=False, retained=False))
        for status in ["stopped", "interrupted", "incomplete", "needs_intervention", "completed"]:
            self.assertIn("delete", available_job_actions({"status": status}, controlled=False, browser=False, retained=False))
            self.assertNotIn("delete", available_job_actions({"status": status}, controlled=False, browser=True, retained=True))
