from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.config import DATABASE_FILENAME
from app.data_layout import upgrade_layout


class DataLayoutTests(unittest.TestCase):
    def _database(self, root: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(root / DATABASE_FILENAME)
        connection.executescript(
            """
            CREATE TABLE accounts(id TEXT PRIMARY KEY, profile_dir TEXT, storage_state_path TEXT);
            CREATE TABLE jobs(id TEXT PRIMARY KEY, account_id TEXT);
            CREATE TABLE learning_runs(id TEXT PRIMARY KEY, account_id TEXT);
            CREATE TABLE artifacts(id TEXT PRIMARY KEY, job_id TEXT, path TEXT);
            CREATE TABLE learning_artifacts(id TEXT PRIMARY KEY, run_id TEXT, path TEXT);
            """
        )
        return connection

    def test_legacy_layout_is_moved_under_accounts_and_paths_are_rebound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account_id = "a" * 32
            (root / "profiles" / account_id).mkdir(parents=True)
            (root / "profiles" / account_id / "storage-state.json").write_text("{}", encoding="utf-8")
            (root / "profiles" / account_id / "keep.txt").write_text("profile", encoding="utf-8")
            (root / "attendance-assets" / account_id).mkdir(parents=True)
            (root / "attendance-assets" / account_id / "qr.png").write_bytes(b"png")

            attempt = root / "task-workspaces" / "assignment" / "attempt1"
            attempt.mkdir(parents=True)
            (attempt / "manifest.json").write_text(
                json.dumps({"account": {"id": account_id}}), encoding="utf-8"
            )
            (attempt / "checkpoints").mkdir()

            job_id = "b" * 32
            (root / "artifacts" / job_id).mkdir(parents=True)
            (root / "artifacts" / job_id / "step.png").write_bytes(b"png")

            connection = self._database(root)
            with connection:
                connection.execute(
                    "INSERT INTO accounts VALUES(?,?,?)",
                    (
                        account_id,
                        str(root / "profiles" / account_id),
                        str(root / "profiles" / account_id / "storage-state.json"),
                    ),
                )
                connection.execute("INSERT INTO jobs VALUES(?,?)", (job_id, account_id))
                connection.execute(
                    "INSERT INTO artifacts VALUES(?,?,?)",
                    ("art1", job_id, str(root / "artifacts" / job_id / "step.png")),
                )
            connection.close()

            counters = upgrade_layout(root)

            browser = root / "accounts" / account_id / "browser"
            self.assertTrue((browser / "keep.txt").is_file())
            self.assertTrue((browser / "storage-state.json").is_file())
            self.assertFalse((root / "profiles" / account_id).exists())
            self.assertTrue(
                (root / "accounts" / account_id / "tasks" / "assignment" / "attempt1" / "checkpoints").is_dir()
            )
            self.assertTrue(
                (root / "accounts" / account_id / "artifacts" / job_id / "step.png").is_file()
            )
            self.assertTrue((root / "accounts" / account_id / "attendance" / "qr.png").is_file())
            self.assertEqual(counters["accounts"], 1)
            self.assertEqual(counters["tasks"], 1)
            self.assertEqual(counters["artifacts"], 1)
            self.assertEqual(counters["attendance"], 1)

            connection = sqlite3.connect(root / DATABASE_FILENAME)
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM accounts").fetchone()
            self.assertEqual(Path(row["profile_dir"]).resolve(), browser.resolve())
            self.assertEqual(Path(row["storage_state_path"]).resolve(), (browser / "storage-state.json").resolve())
            artifact_path = connection.execute("SELECT path FROM artifacts").fetchone()["path"]
            self.assertEqual(
                Path(artifact_path).resolve(),
                (root / "accounts" / account_id / "artifacts" / job_id / "step.png").resolve(),
            )
            connection.close()

    def test_repeated_upgrade_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account_id = "c" * 32
            (root / "profiles" / account_id).mkdir(parents=True)
            connection = self._database(root)
            with connection:
                connection.execute(
                    "INSERT INTO accounts VALUES(?,?,?)",
                    (account_id, str(root / "profiles" / account_id), ""),
                )
            connection.close()

            first = upgrade_layout(root)
            second = upgrade_layout(root)

            self.assertEqual(first["accounts"], 1)
            self.assertEqual(second["accounts"], 0)
            self.assertTrue((root / "accounts" / account_id / "browser").is_dir())

    def test_existing_target_is_preserved_and_source_is_left_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account_id = "d" * 32
            (root / "profiles" / account_id).mkdir(parents=True)
            (root / "profiles" / account_id / "old.txt").write_text("old", encoding="utf-8")
            (root / "accounts" / account_id / "browser").mkdir(parents=True)
            (root / "accounts" / account_id / "browser" / "new.txt").write_text("new", encoding="utf-8")
            connection = self._database(root)
            with connection:
                connection.execute(
                    "INSERT INTO accounts VALUES(?,?,?)",
                    (account_id, str(root / "profiles" / account_id), ""),
                )
            connection.close()

            upgrade_layout(root)

            self.assertTrue((root / "accounts" / account_id / "browser" / "new.txt").is_file())
            self.assertFalse((root / "accounts" / account_id / "browser" / "old.txt").exists())
            self.assertTrue((root / "profiles" / account_id / "old.txt").is_file())

    def test_workspace_without_account_manifest_stays_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt = root / "task-workspaces" / "manual" / "unknown"
            attempt.mkdir(parents=True)
            connection = self._database(root)
            connection.close()

            counters = upgrade_layout(root)

            self.assertEqual(counters["tasks"], 0)
            self.assertTrue((attempt / ".").exists())

    def test_learning_artifacts_are_rebound_to_account_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account_id = "e" * 32
            run_id = "f" * 32
            folder = root / "artifacts" / "learning" / run_id
            folder.mkdir(parents=True)
            (folder / "section-001.png").write_bytes(b"png")
            (root / "profiles" / account_id).mkdir(parents=True)
            connection = self._database(root)
            with connection:
                connection.execute(
                    "INSERT INTO accounts VALUES(?,?,?)",
                    (account_id, str(root / "profiles" / account_id), ""),
                )
                connection.execute("INSERT INTO learning_runs VALUES(?,?)", (run_id, account_id))
                connection.execute(
                    "INSERT INTO learning_artifacts VALUES(?,?,?)",
                    ("la1", run_id, str(folder / "section-001.png")),
                )
            connection.close()

            upgrade_layout(root)

            expected = root / "accounts" / account_id / "artifacts" / run_id / "section-001.png"
            self.assertTrue(expected.is_file())
            connection = sqlite3.connect(root / DATABASE_FILENAME)
            path = connection.execute("SELECT path FROM learning_artifacts").fetchone()[0]
            connection.close()
            self.assertEqual(Path(path).resolve(), expected.resolve())


if __name__ == "__main__":
    unittest.main()
