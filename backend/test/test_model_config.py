from __future__ import annotations

import tempfile
import unittest
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

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
            class MemorySecretStore:
                def __init__(self):
                    self.values = {}

                def put(self, reference, value):
                    self.values[reference] = value

                def get(self, reference):
                    return self.values[reference]

            memory_store = MemorySecretStore()
            secret_store_patch = patch("app.services.agents.config.secret_store_for", return_value=memory_store) if sys.platform == "linux" else nullcontext()
            try:
                with secret_store_patch:
                    effective = main.effective_settings()
                self.assertEqual(effective.openai_model, "primary-model")
                self.assertEqual(
                    effective.openai_fallback_model,
                    "fallback-model",
                )
                self.assertEqual(effective.reasoning_effort, "high")
                self.assertEqual(
                    len(list(settings.secrets_root.glob("*.bin"))),
                    0 if sys.platform == "linux" else 1,
                )
                self.assertNotIn(
                    "api_key",
                    database.get_setting("model_config"),
                )
            finally:
                main.database = original_database


if __name__ == "__main__":
    unittest.main()
