from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import main
from app.config import Settings
from app.db import Database


class ModelConfigTests(unittest.TestCase):
    def test_reasoning_and_secondary_model_are_effective(self) -> None:
        original_database = main.database
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(data_root=Path(temp_dir))
            settings.ensure_directories()
            database = Database(settings)
            database.initialize()
            database.set_setting(
                "model_config",
                {
                    "provider_name": "test",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret",
                    "models": ["primary-model", "fallback-model"],
                    "reasoning_effort": "high",
                },
            )
            main.database = database
            try:
                effective = main.effective_settings()
                self.assertEqual(effective.openai_model, "primary-model")
                self.assertEqual(
                    effective.openai_fallback_model,
                    "fallback-model",
                )
                self.assertEqual(effective.reasoning_effort, "high")
            finally:
                main.database = original_database


if __name__ == "__main__":
    unittest.main()
