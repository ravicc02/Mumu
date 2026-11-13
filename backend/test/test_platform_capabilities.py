from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.db import Database
from app.services.learning.routes import learning_router
from app.services.learning.store import LearningStore
from app.services.platforms.catalog import require_business


class CapabilityContractTests(unittest.TestCase):
    def test_supported_businesses_are_explicit_and_unknown_types_fail_closed(self):
        self.assertTrue(require_business("chaoxing", "assignments")["assignments"])
        self.assertTrue(require_business("chaoxing", "learning")["learning"])
        self.assertTrue(require_business("educoder", "assignments")["assignments"])
        self.assertTrue(require_business("icourse", "learning")["learning"])
        for platform, business in (("educoder", "learning"), ("icourse", "assignments"), ("chaoxing", "unknown")):
            with self.assertRaises(ValueError): require_business(platform, business)


class CapabilityApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.db = Database(self.settings); self.db.initialize()
        self.store = LearningStore(self.db); self.store.initialize()
        self.educoder = self.db.create_account("Same name", "educoder", self.settings)
        self.mooc = self.db.create_account("Same name", "icourse", self.settings)
        for account in (self.educoder, self.mooc): self.db.update_account(account["id"], status="ready")
        self.legacy = self.store.upsert_courses(self.educoder["id"], [{"courseId":"legacy","classId":"","courseName":"Legacy","url":"https://www.educoder.net/"}], 0)[0]
        self.guard = Mock()
        self.service = SimpleNamespace(_database=lambda:self.db, store=self.store, payload=lambda run:run, collect=AsyncMock(return_value=[]), start=Mock())
        self.app = FastAPI()
        self.app.include_router(learning_router(self.service, self.guard, self.guard, lambda account:asyncio.Lock()))

    async def asyncTearDown(self): self.temp.cleanup()

    async def test_educoder_cannot_list_collect_import_or_start_course_learning(self):
        account = self.educoder["id"]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://fixture") as client:
            responses = [
                await client.get("/api/learning/courses",params={"account_id":account}),
                await client.get("/api/learning/runs",params={"account_id":account}),
                await client.post(f"/api/learning/accounts/{account}/collect"),
                await client.post("/api/learning/courses",json={"account_id":account,"name":"Bad import","url":"https://www.educoder.net/"}),
                await client.post("/api/learning/runs",json={"course_id":self.legacy["id"]}),
            ]
        for response in responses:
            self.assertEqual(response.status_code,409,response.text)
            self.assertIn("头歌当前未接入网课",response.json()["detail"])
        self.guard.assert_not_called()
        self.service.collect.assert_not_awaited()
        self.service.start.assert_not_called()
        self.assertEqual(len(self.store.courses(account,0)),1)

    async def test_supported_learning_platform_can_import_and_read_its_own_courses(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://fixture") as client:
            added=await client.post("/api/learning/courses",json={"account_id":self.mooc["id"],"name":"Allowed","url":"https://www.icourse163.org/course/123"})
            self.assertEqual(added.status_code,200,added.text)
            listed=await client.get("/api/learning/courses",params={"account_id":self.mooc["id"]})
            self.assertEqual([row["id"] for row in listed.json()],[added.json()["id"]])
            self.assertTrue(all(row["account_id"]==self.mooc["id"] for row in listed.json()))
            collected=await client.post(f"/api/learning/accounts/{self.mooc['id']}/collect?academic_year=0")
            self.assertEqual(collected.status_code,200,collected.text)
            self.service.collect.assert_awaited_once_with(self.mooc["id"],0)

    async def test_historical_records_remain_readable_but_capability_does_not_allow_retry(self):
        run=self.store.create_run(self.legacy["id"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://fixture") as client:
            history=await client.get("/api/learning/runs/"+run["id"])
            self.assertEqual(history.status_code,200,history.text)
            retried=await client.post("/api/learning/runs/"+run["id"]+"/retry")
            self.assertEqual(retried.status_code,409,retried.text)
        self.assertEqual(self.store.run(run["id"])["id"],run["id"])

    async def test_learning_only_account_cannot_use_assignment_list_or_collection(self):
        from app import main
        with patch.object(main,"database",self.db):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url="http://fixture") as client:
                responses=[
                    await client.get("/api/assignments",params={"account_id":self.mooc["id"]}),
                    await client.get("/api/semesters",params={"account_id":self.mooc["id"]}),
                    await client.post(f"/api/accounts/{self.mooc['id']}/collect"),
                ]
        for response in responses:
            self.assertEqual(response.status_code,409,response.text)
            self.assertIn("未接入作业",response.json()["detail"])
