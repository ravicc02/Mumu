from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from app.config import Settings
from app.db import Database
from app import main
from app.data_backups import DataBackupRequest, create_backup


class DataBackupRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = Settings(data_root=self.root)
        self.settings.ensure_directories()
        self.db = Database(self.settings)
        self.db.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_restore_request_is_deferred_and_duplicate_rejected(self):
        backup = create_backup(self.root, self.db, DataBackupRequest())
        with patch.object(main, "settings", self.settings), patch.object(main, "database", self.db), patch.object(main, "_ensure_data_idle"):
            response = asyncio.run(main.request_data_restore(main.DataRestoreRequest(backup_id=backup["id"])))
            self.assertTrue(response["restart_required"])
            self.assertTrue((self.root / "pending-restore.json").exists())
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(main.request_data_restore(main.DataRestoreRequest(backup_id=backup["id"])))
            self.assertEqual(caught.exception.status_code, 409)

    def test_active_task_rejects_maintenance(self):
        with patch.object(main, "database", self.db), patch.object(main, "_active_data_state", return_value=({"active-job"}, set())):
            with self.assertRaises(HTTPException) as caught:
                main._ensure_data_idle()
            self.assertEqual(caught.exception.status_code, 409)

    def test_policy_is_validated_and_persisted(self):
        with patch.object(main, "database", self.db):
            asyncio.run(main.update_backup_policy(main.BackupPolicy(frequency="weekly", keep=2)))
            self.assertEqual(asyncio.run(main.get_backup_policy()), {"frequency": "weekly", "keep": 2})
