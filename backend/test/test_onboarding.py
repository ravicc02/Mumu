from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import main
from app.config import Settings
from app.db import Database
from app.onboarding import load_onboarding


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        self.database_override = patch.object(main, "database", self.database)
        self.database_override.start()

    def tearDown(self):
        self.database_override.stop()
        self.temp.cleanup()

    async def test_first_run_completion_is_persisted_without_credentials(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            initial = await client.get("/api/onboarding")
            self.assertEqual(initial.status_code, 200)
            self.assertFalse(initial.json()["completed"])

            rejected = await client.put("/api/onboarding", json={
                "model": "configured",
                "platform": "authorized",
                "api_key": "never-save",
            })
            self.assertEqual(rejected.status_code, 422)

            completed = await client.put("/api/onboarding", json={
                "model": "configured",
                "platform": "authorized",
            })
            self.assertEqual(completed.status_code, 200)
            self.assertTrue(completed.json()["completed"])
            self.assertEqual((await client.get("/api/onboarding")).json(), completed.json())

        restarted = Database(self.settings)
        restarted.initialize()
        persisted = load_onboarding(restarted)
        self.assertTrue(persisted["completed"])
        self.assertEqual((persisted["model"], persisted["platform"]), ("configured", "authorized"))
        self.assertNotIn("never-save", str(restarted.get_setting("onboarding", {})))

    async def test_corrupt_completion_state_fails_closed(self):
        self.database.set_setting("onboarding", {"version": 1, "completed": "yes"})
        state = load_onboarding(self.database)
        self.assertFalse(state["completed"])
