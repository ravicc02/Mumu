from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.main import DataBackupRequest, _create_backup, _data_stats, _optimize_data, _verify_backup


class DataStatsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = Settings(data_root=self.root)
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_stats_report_counts_and_ignore_cache_directories(self) -> None:
        account = self.database.create_account("测试账号", "chaoxing", self.settings)
        account_root = self.root / "accounts" / account["id"]
        (account_root / "tasks").mkdir(parents=True, exist_ok=True)
        (account_root / "browser" / "Cache").mkdir(parents=True, exist_ok=True)
        (account_root / "tasks" / "manifest.json").write_bytes(b"manifest")
        (account_root / "browser" / "Cache" / "large.cache").write_bytes(b"ignored")
        (self.root / "logs").mkdir(exist_ok=True)
        (self.root / "logs" / "main.log").write_bytes(b"log")
        (self.root / "backups").mkdir(exist_ok=True)
        (self.root / "backups" / "snapshot.zip").write_bytes(b"backup")

        stats = _data_stats(self.root, self.database)

        self.assertEqual(stats["counts"]["accounts"], 1)
        self.assertEqual(stats["counts"]["assignments"], 0)
        self.assertEqual(stats["counts"]["tasks"], 0)
        self.assertEqual(stats["sizes"]["accounts_breakdown"]["tasks"], len(b"manifest"))
        self.assertEqual(stats["sizes"]["accounts_breakdown"]["browser"], 0)
        self.assertEqual(stats["sizes"]["logs"], len(b"log"))
        self.assertEqual(stats["sizes"]["backups"], len(b"backup"))
        self.assertEqual(stats["sizes"]["database"], self.database.path.stat().st_size)

    def test_optimize_removes_old_logs_and_browser_cache_only(self) -> None:
        import os
        import time

        old = time.time() - 40 * 86400
        archive = self.root / "logs" / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        old_log = archive / "old.log"
        old_log.write_bytes(b"old log")
        os.utime(old_log, (old, old))

        account = self.database.create_account("清理账号", "chaoxing", self.settings)
        browser = self.root / "accounts" / account["id"] / "browser"
        cache = browser / "Cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "entry").write_bytes(b"cache")
        preserved = browser / "Cookies"
        preserved.write_bytes(b"keep")
        workspace = self.root / "accounts" / account["id"] / "tasks" / "assignment" / "old-job"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "input.txt").write_bytes(b"workspace")
        artifact = self.root / "accounts" / account["id"] / "artifacts" / "old-job"
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "capture.png").write_bytes(b"artifact")
        for path in (workspace, artifact):
            os.utime(path, (old, old))

        logs_result = _optimize_data(self.root, self.database, "logs", 30)
        workspace_result = _optimize_data(self.root, self.database, "workspaces", 30)
        artifact_result = _optimize_data(self.root, self.database, "artifacts", 30)
        cache_result = _optimize_data(self.root, self.database, "browser-cache", 30)

        self.assertEqual(logs_result["released_bytes"], len(b"old log"))
        self.assertFalse(old_log.exists())
        self.assertEqual(workspace_result["released_bytes"], len(b"workspace"))
        self.assertFalse(workspace.exists())
        self.assertEqual(artifact_result["released_bytes"], len(b"artifact"))
        self.assertFalse(artifact.exists())
        self.assertEqual(cache_result["released_bytes"], len(b"cache"))
        self.assertFalse(cache.exists())
        self.assertTrue(preserved.exists())

    def test_restore_roundtrip_and_snapshot(self) -> None:
        from app.data_backups import restore_backup
        self.database.create_account("恢复账号", "chaoxing", self.settings)
        result = _create_backup(self.root, self.database, DataBackupRequest())
        self.database.create_account("新增账号", "chaoxing", self.settings)
        restored = restore_backup(self.root, self.database, result["id"])
        self.assertEqual(len(self.database.list_accounts()), 1)
        self.assertTrue((self.root / "backups" / restored["snapshot"]["id"]).exists())

    def test_restore_rejects_schema_change_before_snapshot(self) -> None:
        from app.data_backups import restore_backup
        backup = _create_backup(self.root, self.database, DataBackupRequest())
        with self.database.connect() as connection:
            connection.execute("CREATE TABLE new_schema (id TEXT)")
        with self.assertRaisesRegex(ValueError, "表结构"):
            restore_backup(self.root, self.database, backup["id"])
        self.assertFalse(list((self.root / "backups").glob("before-restore-*.zip")))

    def test_restore_rolls_back_files_on_copy_failure(self) -> None:
        from unittest.mock import patch
        import shutil
        from app.data_backups import restore_backup
        account = self.database.create_account("回滚账号", "chaoxing", self.settings)
        state = Path(account["storage_state_path"])
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("before", encoding="utf-8")
        backup = _create_backup(self.root, self.database, DataBackupRequest())
        state.write_text("current", encoding="utf-8")
        copy = shutil.copy2
        failed = False

        def fail_once(source, target, *args, **kwargs):
            nonlocal failed
            if Path(target) == state and not failed:
                failed = True
                raise OSError("模拟写入失败")
            return copy(source, target, *args, **kwargs)

        with patch("app.data_backups.shutil.copy2", side_effect=fail_once):
            with self.assertRaises(OSError):
                restore_backup(self.root, self.database, backup["id"])
        self.assertEqual(state.read_text(encoding="utf-8"), "current")
        self.assertEqual(len(self.database.list_accounts()), 1)

    def test_automatic_backup_due_and_rotation(self) -> None:
        from datetime import datetime, timedelta, timezone
        from app.data_backups import automatic_backup
        self.database.set_setting("data_backup_policy", {"frequency": "daily", "keep": 1})
        now = datetime.now(timezone.utc)
        self.assertIsNotNone(automatic_backup(self.root, self.database, now=now))
        self.assertIsNone(automatic_backup(self.root, self.database, now=now))
        self.assertIsNotNone(automatic_backup(self.root, self.database, now=now + timedelta(days=2)))
        self.assertEqual(len(list((self.root / "backups").glob("auto-*.zip"))), 1)

    def test_rejects_unlisted_archive_files_without_mutation(self) -> None:
        import zipfile
        from app.data_backups import restore_backup, archive_name
        result = _create_backup(self.root, self.database, DataBackupRequest())
        backup = self.root / "backups" / result["id"]
        with zipfile.ZipFile(backup, "a") as archive:
            archive.writestr("accounts/unknown/tasks/evil.txt", b"evil")
        self.assertFalse(_verify_backup(backup, backup.parent)["valid"])
        with self.assertRaises(ValueError):
            restore_backup(self.root, self.database, result["id"])
        for name in ("../escape", "accounts/a/secrets/key", "accounts/a/artifacts/file:stream", "/accounts/a/account.json"):
            with self.assertRaises(ValueError):
                archive_name(name)
        self.assertEqual(len(list(backup.parent.glob("before-restore-*.zip"))), 0)

    def test_backup_manifest_verifies_selected_content_without_secrets_or_workspace(self) -> None:
        account = self.database.create_account("备份账号", "chaoxing", self.settings)
        account_root = self.root / "accounts" / account["id"]
        (account_root / "tasks" / "assignment" / "job").mkdir(parents=True, exist_ok=True)
        (account_root / "tasks" / "assignment" / "job" / "draft.txt").write_bytes(b"draft")
        (account_root / "artifacts" / "job").mkdir(parents=True, exist_ok=True)
        (account_root / "artifacts" / "job" / "result.txt").write_bytes(b"result")
        self.settings.secrets_root.mkdir(parents=True, exist_ok=True)
        (self.settings.secrets_root / "provider.json").write_bytes(b"secret")

        result = _create_backup(
            self.root,
            self.database,
            DataBackupRequest(include_accounts=True, include_artifacts=True),
        )
        backup = self.root / "backups" / result["id"]
        checked = _verify_backup(backup, self.root / "backups")

        self.assertTrue(checked["valid"])
        with __import__("zipfile").ZipFile(backup) as archive:
            names = set(archive.namelist())
        self.assertIn("manifest.json", names)
        self.assertIn("database/mumu.sqlite3", names)
        self.assertTrue(any(name.endswith("result.txt") for name in names))
        self.assertFalse(any("tasks/" in name for name in names))
        self.assertFalse(any("secrets/" in name for name in names))


if __name__ == "__main__":
    unittest.main()
