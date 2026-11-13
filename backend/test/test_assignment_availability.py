import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from app.config import Settings
from app.db import Database
from app.services.assignments.availability import assignment_deadline, assignment_expired
from app.services.notifications.collection import eligible


class AssignmentAvailabilityTests(unittest.TestCase):
    def test_shared_deadline_contract(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/assignment-deadlines.json").read_text(encoding="utf-8"))
        for fixture in fixtures:
            with self.subTest(fixture["name"]):
                now = datetime.fromisoformat(fixture["now"].replace("Z", "+00:00"))
                self.assertEqual(assignment_expired(fixture["item"], now), fixture["expired"])
                if fixture.get("unknown"):
                    self.assertIsNone(assignment_deadline(fixture["item"], now))

    def test_expired_records_are_not_submittable_but_keep_their_history(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(data_root=Path(folder)); settings.ensure_directories()
            db = Database(settings); db.initialize()
            account = db.create_account("account", "chaoxing", settings)
            identifier = db.upsert_assignment(account["id"], "chaoxing", {"courseId":"course", "classId":"class", "workId":"work", "status":"unfinished", "submittable":True, "deadlineText":"2000-01-01 10:00"}, settings)
            self.assertFalse(db.get_assignment(identifier)["submittable"])
            with db.connect() as connection:
                connection.execute("UPDATE assignments SET submittable=1 WHERE id=?", (identifier,))
            self.assertEqual(db.list_assignments(account["id"], submittable_only=True), [])
            self.assertEqual(len(db.list_assignments(account["id"], submittable_only=False)), 1)
            self.assertFalse(eligible(db.get_assignment(identifier)))
            from app import main
            with patch.object(main, "database", db):
                with self.assertRaises(HTTPException) as context: main.ensure_assignment_execution_allowed(identifier)
                self.assertEqual(context.exception.status_code, 409)
