import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.services import chaoxing
from app.services.account_identity import finish_chaoxing_authorization, read_identity, save_identity
from app.services.learning.store import LearningStore


class AccountIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.root.name))
        self.settings.ensure_directories()
        self.db = Database(self.settings)
        self.db.initialize()
        LearningStore(self.db).initialize()
        self.account = self.db.create_account("legacy alias", "chaoxing", self.settings)
        self.identity = {"name": "Platform Name", "platform_user_id": "123", "avatar_url": "https://photo.chaoxing.com/avatar.jpg"}
        self.page = SimpleNamespace(context=SimpleNamespace(storage_state=AsyncMock(return_value={"cookies": [], "origins": []})))
        self.read = patch("app.services.account_identity.read_identity", AsyncMock(return_value=self.identity))
        self.avatar = patch("app.services.account_identity.cache_avatar", AsyncMock(return_value="image/jpeg"))
        self.read.start(); self.avatar.start()

    async def asyncTearDown(self):
        self.read.stop(); self.avatar.stop()
        self.root.cleanup()

    def add_assignment(self, account_id):
        return self.db.upsert_assignment(account_id, "chaoxing", {"courseId": "course", "classId": "class", "workId": "work", "courseName": "Course", "title": "Assignment", "taskUrl": "https://mooc1.chaoxing.com/work", "submittable": True}, self.settings)

    async def test_authorization_reads_identity_and_keeps_existing_business_records(self):
        assignment = self.add_assignment(self.account["id"])
        self.db.update_account(self.account["id"], platform_user_id="123", auto_collect_enabled=1)
        result = await finish_chaoxing_authorization(self.db, self.account["id"], self.page)
        self.assertEqual((result["id"], result["name"], result["status"], result["avatar_mime"]), (self.account["id"], "Platform Name", "ready", "image/jpeg"))
        self.assertEqual(result["auto_collect_enabled"], 1)
        self.assertEqual(self.db.get_assignment(assignment)["account_id"], self.account["id"])
        self.assertTrue(result["identity_verified_at"])
        self.assertTrue(result["last_login_at"])
        self.assertEqual(json.loads(Path(result["storage_state_path"]).read_text()), {"cookies": [], "origins": []})

    async def test_duplicate_authorization_updates_original_instead_of_adding_record(self):
        self.db.update_account(self.account["id"], platform_user_id="123", identity_verified_at="old", auto_collect_enabled=1)
        assignment = self.add_assignment(self.account["id"])
        course = LearningStore(self.db).upsert_courses(self.account["id"], [{"courseId": "one", "classId": "class", "courseName": "Course", "url": "https://mooc1.chaoxing.com/course"}], 2026)[0]
        pending = self.db.create_account("学习通 · 待授权", "chaoxing", self.settings)
        result = await finish_chaoxing_authorization(self.db, pending["id"], self.page)
        self.assertEqual(result["id"], self.account["id"])
        self.assertEqual(len(self.db.list_accounts()), 1)
        self.assertEqual(self.db.get_assignment(assignment)["account_id"], result["id"])
        self.assertEqual(LearningStore(self.db).course(course["id"])["account_id"], result["id"])
        self.assertEqual(result["auto_collect_enabled"], 1)
        with self.assertRaises(KeyError): self.db.get_account(pending["id"])

    async def test_legacy_account_is_matched_by_its_verified_saved_platform_user_id(self):
        Path(self.account["storage_state_path"]).write_text(json.dumps({"cookies": [{"name": "_uid", "value": "123", "domain": ".chaoxing.com"}]}))
        pending = self.db.create_account("pending", "chaoxing", self.settings)
        result = await finish_chaoxing_authorization(self.db, pending["id"], self.page)
        self.assertEqual(result["id"], self.account["id"])
        self.assertEqual(result["platform_user_id"], "123")

    async def test_wrong_user_cannot_replace_the_original_saved_session(self):
        self.db.update_account(self.account["id"], platform_user_id="456")
        state = Path(self.account["storage_state_path"])
        state.write_text('{"cookies":[],"original":true}')
        before = state.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "不一致"):
            await finish_chaoxing_authorization(self.db, self.account["id"], self.page)
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual(self.db.get_account(self.account["id"])["name"], "legacy alias")
        self.page.context.storage_state.assert_not_awaited()

    async def test_legacy_wrong_user_is_rejected_before_session_overwrite(self):
        state = Path(self.account["storage_state_path"])
        state.write_text(json.dumps({"cookies": [{"name": "_uid", "value": "456", "domain": ".chaoxing.com"}]}))
        with self.assertRaisesRegex(RuntimeError, "不一致"):
            await finish_chaoxing_authorization(self.db, self.account["id"], self.page)
        self.assertIn("456", state.read_text())

    async def test_pending_record_with_business_data_is_never_discarded(self):
        self.db.update_account(self.account["id"], platform_user_id="123")
        pending = self.db.create_account("pending", "chaoxing", self.settings)
        assignment = self.add_assignment(pending["id"])
        with self.assertRaisesRegex(RuntimeError, "业务数据"):
            await finish_chaoxing_authorization(self.db, pending["id"], self.page)
        self.assertEqual(self.db.get_assignment(assignment)["account_id"], pending["id"])
        with self.assertRaises(RuntimeError): self.db.discard_pending_account(pending["id"])

    async def test_pending_record_with_course_history_is_not_cascade_deleted(self):
        course = LearningStore(self.db).upsert_courses(self.account["id"], [{"courseId": "one", "classId": "class", "courseName": "Course", "url": "https://mooc1.chaoxing.com/course"}], 2026)[0]
        with self.assertRaises(RuntimeError): self.db.discard_pending_account(self.account["id"])
        self.assertEqual(LearningStore(self.db).course(course["id"])["account_id"], self.account["id"])

    async def test_read_failure_does_not_mark_authorization_successful(self):
        with patch("app.services.account_identity.read_identity", AsyncMock(return_value={"name": "", "platform_user_id": ""})):
            with self.assertRaisesRegex(RuntimeError, "未读取完整"):
                await finish_chaoxing_authorization(self.db, self.account["id"], self.page)
        self.assertNotEqual(self.db.get_account(self.account["id"])["status"], "ready")

    async def test_identity_metadata_sync_does_not_replace_browser_session(self):
        state = Path(self.account["storage_state_path"])
        state.write_text('{"cookies":[],"original":true}')
        before = state.read_bytes()
        await save_identity(self.db, self.account["id"], self.page)
        self.assertEqual(state.read_bytes(), before)
        self.page.context.storage_state.assert_not_awaited()

    async def test_redirected_authorization_status_points_to_existing_record(self):
        import app.main as server
        with patch.object(server, "database", self.db), patch.dict(chaoxing._login_status, {"removed-pending": {"status": "success", "account_id": self.account["id"], "message": "授权已更新"}}):
            client = TestClient(server.app)
            response = client.get("/api/accounts/removed-pending/login-status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["account_id"], self.account["id"])
            renamed = client.patch("/api/accounts/" + self.account["id"], json={"name": "custom"})
            self.assertEqual(renamed.status_code, 409)
            self.assertEqual(self.db.get_account(self.account["id"])["name"], "legacy alias")

    async def test_login_worker_finishes_on_the_canonical_account_and_removes_only_its_temporary_profile(self):
        self.db.update_account(self.account["id"], platform_user_id="123", status="ready")
        pending = self.db.create_account("pending", "chaoxing", self.settings)
        login_page = SimpleNamespace(goto=AsyncMock())
        login_context = SimpleNamespace(pages=[login_page], storage_state=AsyncMock(return_value={"cookies": [], "origins": []}), close=AsyncMock())
        verify_context = SimpleNamespace(new_page=AsyncMock(return_value=self.page))
        verify_browser = SimpleNamespace(new_context=AsyncMock(return_value=verify_context), close=AsyncMock())
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=AsyncMock(return_value=login_context), launch=AsyncMock(return_value=verify_browser)))
        manager = MagicMock(__aenter__=AsyncMock(return_value=playwright), __aexit__=AsyncMock())
        try:
            with patch.object(chaoxing, "async_playwright", return_value=manager), patch.object(chaoxing, "_verify_authenticated", AsyncMock(return_value=True)), patch.object(chaoxing.asyncio, "sleep", AsyncMock()):
                await chaoxing._login_account(self.db, pending["id"], self.settings)
            self.assertEqual(chaoxing.login_status(pending["id"])["account_id"], self.account["id"])
            self.assertEqual(chaoxing.login_status(pending["id"])["status"], "success")
            login_context.storage_state.assert_awaited_once_with()
            self.assertFalse(Path(pending["profile_dir"]).exists())
            self.assertTrue(Path(self.account["profile_dir"]).exists())
            verify_browser.close.assert_awaited_once()
        finally:
            chaoxing._login_status.pop(pending["id"], None)


class ChaoxingIdentityReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_name_avatar_and_stable_uid_come_from_authenticated_platform(self):
        response = SimpleNamespace(ok=True, url="https://i.chaoxing.com/base?ws=1", text=AsyncMock(return_value='<div class="user-name">  平台姓名  </div><img class="head-img" src="https://photo.chaoxing.com/profile.jpg">'))
        page = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(get=AsyncMock(return_value=response)), cookies=AsyncMock(return_value=[{"name": "_uid", "value": "123"}])))
        result = await read_identity("chaoxing", page)
        self.assertEqual(result, {"name": "平台姓名", "platform_user_id": "123", "avatar_url": "https://photo.chaoxing.com/profile.jpg"})
        response.ok = False
        with self.assertRaises(RuntimeError): await read_identity("chaoxing", page)
