from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.config import (
    default_data_root,
    legacy_data_root,
    migrate_database_filename,
    migrate_legacy_data_root,
    settings,
)
from app.product import (
    DATA_DIRECTORY,
    DATABASE_FILENAME,
    LEGACY_DATA_DIRECTORY,
    LEGACY_DATABASE_FILENAME,
    PRODUCT_NAME,
)


class ProductCompatibilityTests(unittest.TestCase):
    def test_product_name_and_legacy_storage_identity_are_distinct(self):
        self.assertEqual(PRODUCT_NAME, "Mumu")
        self.assertEqual(DATA_DIRECTORY, ".mumu")
        self.assertEqual(LEGACY_DATA_DIRECTORY, "AIAssistantStudio")
        self.assertEqual(DATABASE_FILENAME, "mumu.sqlite3")
        self.assertEqual(LEGACY_DATABASE_FILENAME, "answer-studio.sqlite3")

    def test_new_environment_name_wins_and_legacy_name_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory) / "primary"
            legacy = Path(directory) / "legacy"
            with patch.dict(
                os.environ,
                {
                    "MUMU_DATA": str(primary),
                    "ANSWER_STUDIO_DATA": str(legacy),
                },
                clear=False,
            ):
                self.assertEqual(default_data_root(), primary.resolve())
            with patch.dict(os.environ, {"ANSWER_STUDIO_DATA": str(legacy)}, clear=False):
                with patch.dict(os.environ, {"MUMU_DATA": ""}, clear=False):
                    self.assertEqual(default_data_root(), legacy.resolve())

    def test_default_data_root_uses_the_user_home_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            with patch("app.config.Path.home", return_value=home):
                with patch.dict(
                    os.environ,
                    {"MUMU_DATA": "", "ANSWER_STUDIO_DATA": ""},
                    clear=False,
                ):
                    self.assertEqual(default_data_root(), (home / ".mumu").resolve())

    def test_legacy_data_is_copied_to_the_new_root_without_removing_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "legacy"
            target = Path(directory) / "home" / ".mumu"
            (source / "profiles").mkdir(parents=True)
            (source / "profiles" / "account.json").write_text("session", encoding="utf-8")
            with closing(sqlite3.connect(source / DATABASE_FILENAME)) as connection:
                with connection:
                    connection.execute("CREATE TABLE accounts(profile_dir TEXT, metadata TEXT)")
                    connection.execute(
                        "INSERT INTO accounts VALUES(?, ?)",
                        (
                            str(source / "profiles" / "one"),
                            json.dumps({"artifact": str(source / "artifacts" / "one.png")}),
                        ),
                    )
            target.mkdir(parents=True)
            with patch.dict(
                os.environ,
                {"MUMU_DATA": "", "ANSWER_STUDIO_DATA": ""},
                clear=False,
            ):
                migrated = migrate_legacy_data_root(target, source=source)
            self.assertEqual(migrated, target.resolve())
            self.assertEqual((target / "profiles" / "account.json").read_text(encoding="utf-8"), "session")
            self.assertTrue((source / DATABASE_FILENAME).is_file())
            with closing(sqlite3.connect(target / DATABASE_FILENAME)) as connection:
                profile_dir, metadata = connection.execute(
                    "SELECT profile_dir, metadata FROM accounts"
                ).fetchone()
            self.assertEqual(profile_dir, str(target.resolve() / "profiles" / "one"))
            self.assertEqual(json.loads(metadata)["artifact"], str(target.resolve() / "artifacts" / "one.png"))

    def test_existing_new_data_root_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "legacy"
            target = Path(directory) / "home" / ".mumu"
            source.mkdir()
            target.mkdir(parents=True)
            with closing(sqlite3.connect(source / DATABASE_FILENAME)) as connection:
                with connection:
                    connection.execute("CREATE TABLE source(value TEXT)")
                    connection.execute("INSERT INTO source VALUES('legacy')")
            with closing(sqlite3.connect(target / DATABASE_FILENAME)) as connection:
                with connection:
                    connection.execute("CREATE TABLE current(value TEXT)")
                    connection.execute("INSERT INTO current VALUES('current')")
            with patch.dict(
                os.environ,
                {"MUMU_DATA": "", "ANSWER_STUDIO_DATA": ""},
                clear=False,
            ):
                migrate_legacy_data_root(target, source=source)
            with closing(sqlite3.connect(target / DATABASE_FILENAME)) as connection:
                self.assertEqual(connection.execute("SELECT value FROM current").fetchone()[0], "current")

    def test_database_paths_are_rebound_from_a_windows_short_source_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "legacy"
            target = Path(directory) / "home" / ".mumu"
            short_source = source.with_name("LEGACY~1")
            source.mkdir()
            with closing(sqlite3.connect(source / DATABASE_FILENAME)) as connection:
                with connection:
                    connection.execute("CREATE TABLE accounts(profile_dir TEXT)")
                    connection.execute(
                        "INSERT INTO accounts VALUES(?)",
                        (str(short_source / "profiles" / "one"),),
                    )
            target.mkdir(parents=True)
            with patch.dict(
                os.environ,
                {"MUMU_DATA": "", "ANSWER_STUDIO_DATA": ""},
                clear=False,
            ):
                with patch("app.config._path_aliases", return_value=(short_source, source.resolve())):
                    migrate_legacy_data_root(target, source=source)
            with closing(sqlite3.connect(target / DATABASE_FILENAME)) as connection:
                profile_dir = connection.execute("SELECT profile_dir FROM accounts").fetchone()[0]
            self.assertEqual(profile_dir, str(target.resolve() / "profiles" / "one"))

    def test_legacy_root_uses_local_app_data(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}, clear=False):
                self.assertEqual(
                    legacy_data_root(),
                    (Path(directory) / LEGACY_DATA_DIRECTORY).resolve(),
                )

    def test_database_uses_the_product_filename(self):
        self.assertEqual(settings.database_path.name, DATABASE_FILENAME)

    def test_legacy_database_filename_is_renamed_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / LEGACY_DATABASE_FILENAME).write_bytes(b"legacy")
            (root / f"{LEGACY_DATABASE_FILENAME}-wal").write_bytes(b"wal")
            migrate_database_filename(root)
            self.assertFalse((root / LEGACY_DATABASE_FILENAME).exists())
            self.assertFalse((root / f"{LEGACY_DATABASE_FILENAME}-wal").exists())
            self.assertEqual((root / DATABASE_FILENAME).read_bytes(), b"legacy")
            self.assertEqual((root / f"{DATABASE_FILENAME}-wal").read_bytes(), b"wal")
            migrate_database_filename(root)
            self.assertEqual((root / DATABASE_FILENAME).read_bytes(), b"legacy")

    def test_legacy_database_never_overwrites_an_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / LEGACY_DATABASE_FILENAME).write_bytes(b"legacy")
            (root / DATABASE_FILENAME).write_bytes(b"current")
            migrate_database_filename(root)
            self.assertEqual((root / DATABASE_FILENAME).read_bytes(), b"current")
            self.assertEqual((root / LEGACY_DATABASE_FILENAME).read_bytes(), b"legacy")

    def test_empty_current_database_does_not_hide_legacy_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / LEGACY_DATABASE_FILENAME
            with closing(sqlite3.connect(legacy)) as connection:
                with connection:
                    connection.execute("CREATE TABLE accounts (id TEXT)")
                    connection.execute("INSERT INTO accounts VALUES('kept')")
            # A shell created before the rename shipped: valid SQLite, no account table rows.
            with closing(sqlite3.connect(root / DATABASE_FILENAME)):
                pass
            migrate_database_filename(root)
            self.assertFalse(legacy.exists())
            with closing(sqlite3.connect(root / DATABASE_FILENAME)) as connection:
                rows = connection.execute("SELECT id FROM accounts").fetchall()
            self.assertEqual(rows, [("kept",)])

    def test_current_database_with_rows_wins_over_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / LEGACY_DATABASE_FILENAME
            current = root / DATABASE_FILENAME
            for path, marker in ((legacy, "legacy"), (current, "current")):
                with closing(sqlite3.connect(path)) as connection:
                    with connection:
                        connection.execute("CREATE TABLE accounts (id TEXT)")
                        connection.execute("INSERT INTO accounts VALUES(?)", (marker,))
            migrate_database_filename(root)
            self.assertTrue(legacy.exists())
            with closing(sqlite3.connect(current)) as connection:
                self.assertEqual(connection.execute("SELECT id FROM accounts").fetchall(), [("current",)])

    def test_legacy_root_migration_renames_the_database(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            target = Path(directory) / "target"
            source.mkdir()
            with closing(sqlite3.connect(source / LEGACY_DATABASE_FILENAME)) as connection:
                with connection:
                    connection.execute("CREATE TABLE accounts (profile_dir TEXT)")
                    connection.execute(
                        "INSERT INTO accounts VALUES(?)",
                        (str(source / "profiles" / "one"),),
                    )
            with patch.dict(
                os.environ,
                {"MUMU_DATA": "", "ANSWER_STUDIO_DATA": ""},
                clear=False,
            ):
                migrate_legacy_data_root(target, source=source)
            self.assertTrue((target / DATABASE_FILENAME).is_file())
            self.assertFalse((target / LEGACY_DATABASE_FILENAME).exists())
            with closing(sqlite3.connect(target / DATABASE_FILENAME)) as connection:
                profile_dir = connection.execute("SELECT profile_dir FROM accounts").fetchone()[0]
            self.assertEqual(profile_dir, str(target.resolve() / "profiles" / "one"))


if __name__ == "__main__":
    unittest.main()
