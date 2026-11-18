from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.db import Database
from app.diagnostics.daily import DailyMainLog


LOCAL = timezone(timedelta(hours=8))


class FakeSecretStore:
    def __init__(self):
        self.values: dict[str, str] = {}

    def put(self, reference: str, value: str) -> None:
        self.values[reference] = value

    def get(self, reference: str) -> str:
        return self.values[reference]

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


class SensitiveSettingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.store = FakeSecretStore()
        self.patcher = patch.object(Database, "_setting_secret_store", return_value=self.store)
        self.patcher.start()
        self.database = Database(self.settings)
        self.database.initialize()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def raw(self, key: str):
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["value"])

    def test_setting_write_stores_reference_and_reads_plaintext(self):
        self.database.set_setting("weixin_binding", {"token": "t-secret", "user_id": "u1"})
        stored = self.raw("weixin_binding")
        self.assertEqual(stored["user_id"], "u1")
        self.assertNotIn("t-secret", json.dumps(stored))
        self.assertIn("secret_ref", stored["token"])
        self.assertEqual(self.database.get_setting("weixin_binding")["token"], "t-secret")

    def test_non_sensitive_setting_is_stored_as_is(self):
        self.database.set_setting("notification_preferences", {"completion_enabled": True})
        self.assertEqual(
            self.raw("notification_preferences"),
            {"completion_enabled": True},
        )

    def test_migration_encrypts_existing_plaintext_once(self):
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, ?)",
                (
                    "attendance_relay",
                    json.dumps({"admin_secret": "plain-secret", "base_url": "http://example.invalid"}),
                    "2026-11-19T00:00:00+00:00",
                ),
            )
        self.database.initialize()
        stored = self.raw("attendance_relay")
        self.assertNotIn("plain-secret", json.dumps(stored))
        self.assertEqual(stored["base_url"], "http://example.invalid")
        self.assertEqual(
            self.database.get_setting("attendance_relay")["admin_secret"], "plain-secret"
        )
        before = json.dumps(stored, sort_keys=True)
        self.database.initialize()
        self.assertEqual(json.dumps(self.raw("attendance_relay"), sort_keys=True), before)

    def test_unavailable_store_keeps_plaintext_instead_of_losing_it(self):
        def unavailable():
            raise RuntimeError("key store unavailable")

        self.database._setting_secret_store = unavailable
        self.database.set_setting("weixin_binding", {"token": "keep-me"})
        self.assertEqual(self.database.get_setting("weixin_binding")["token"], "keep-me")


class ArchiveRotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.journal = DailyMainLog(
            self.settings, clock=lambda: datetime(2026, 11, 19, 12, 0, tzinfo=LOCAL)
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_archive_rotates_when_exceeding_limit(self):
        self.journal.prepare()
        self.journal.ARCHIVE_MAX_BYTES = 32
        self.journal.archive.write_bytes(b'{"code":"history"}\n' * 4)
        self.journal.prepare()
        archived = sorted((self.settings.logs_root / "archive").glob("oldmain-*.log"))
        self.assertEqual(len(archived), 1)
        self.assertIn(b"history", archived[0].read_bytes())
        self.assertEqual(self.journal.archive.stat().st_size, 0)

    def test_rotation_is_skipped_below_limit(self):
        self.journal.prepare()
        self.journal.ARCHIVE_MAX_BYTES = 1024 * 1024
        self.journal.archive.write_bytes(b'{"code":"history"}\n')
        self.journal.prepare()
        self.assertFalse((self.settings.logs_root / "archive").exists())
        self.assertIn(b"history", self.journal.archive.read_bytes())

    def test_second_rotation_does_not_overwrite_the_first(self):
        self.journal.prepare()
        self.journal.ARCHIVE_MAX_BYTES = 32
        self.journal.archive.write_bytes(b'{"code":"first"}\n' * 4)
        self.journal.prepare()
        self.journal.archive.write_bytes(b'{"code":"second"}\n' * 4)
        self.journal.prepare()
        archived = sorted((self.settings.logs_root / "archive").glob("oldmain-*.log"))
        self.assertEqual(len(archived), 2)
        combined = b"".join(path.read_bytes() for path in archived)
        self.assertIn(b"first", combined)
        self.assertIn(b"second", combined)


if __name__ == "__main__":
    unittest.main()
