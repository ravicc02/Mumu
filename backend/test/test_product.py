from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import default_data_root, settings
from app.product import DATABASE_FILENAME, LEGACY_DATA_DIRECTORY, PRODUCT_NAME


class ProductCompatibilityTests(unittest.TestCase):
    def test_product_name_and_legacy_storage_identity_are_distinct(self):
        self.assertEqual(PRODUCT_NAME, "Mumu")
        self.assertEqual(LEGACY_DATA_DIRECTORY, "AIAssistantStudio")
        self.assertEqual(DATABASE_FILENAME, "answer-studio.sqlite3")

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

    def test_database_keeps_the_legacy_filename(self):
        self.assertEqual(settings.database_path.name, DATABASE_FILENAME)


if __name__ == "__main__":
    unittest.main()
