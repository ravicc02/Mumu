from __future__ import annotations

import tempfile
import unittest
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

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


class ModelConfigApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp_dir = Path(self._temp.name)
        self.settings = Settings(data_root=temp_dir, openai_api_key="")
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        self._database_patch = patch.object(main, "database", self.database)
        self._settings_patch = patch.object(main, "settings", self.settings)
        self._database_patch.start()
        self._settings_patch.start()
        self.addCleanup(self._settings_patch.stop)
        self.addCleanup(self._database_patch.stop)
        self.addCleanup(self._temp.cleanup)
        if sys.platform == "linux":
            class MemorySecretStore:
                def __init__(self): self.values = {}
                def put(self, reference, value): self.values[reference] = value
                def get(self, reference): return self.values[reference]
            secrets = patch("app.services.agents.config.secret_store_for", return_value=MemorySecretStore())
            secrets.start()
            self.addCleanup(secrets.stop)
        self.client = TestClient(main.app)

    def test_multi_provider_round_trip(self) -> None:
        response = self.client.put(
            "/api/model-config",
            json={
                "reasoning_effort": "medium",
                "providers": [
                    {
                        "name": "primary",
                        "base_url": "https://a.test/v1",
                        "api_key": "k1",
                        "models": ["m-a"],
                        "use_responses_api": True,
                    },
                    {
                        "name": "backup",
                        "base_url": "https://b.test/v1",
                        "api_key": "k2",
                        "models": ["m-b"],
                        "api_format": "anthropic",
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        providers = response.json()["providers"]
        self.assertEqual([item["id"] for item in providers], ["primary", "backup"])
        self.assertTrue(providers[0]["use_responses_api"])
        self.assertEqual(providers[1]["api_format"], "anthropic")

        fetched = self.client.get("/api/model-config")
        self.assertEqual(fetched.status_code, 200)
        body = fetched.json()
        self.assertEqual([item["name"] for item in body["providers"]], ["primary", "backup"])
        self.assertTrue(body["providers"][0]["default"])
        self.assertFalse(body["providers"][1]["default"])
        self.assertTrue(body["providers"][0]["api_key_configured"])

    def test_single_provider_payload_still_supported(self) -> None:
        response = self.client.put(
            "/api/model-config",
            json={
                "provider_name": "legacy",
                "base_url": "https://legacy.test/v1",
                "api_key": "k",
                "models": ["m-1", "m-2"],
                "reasoning_effort": "high",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["models"], ["m-1", "m-2"])

    def test_missing_address_is_rejected_with_actionable_error(self) -> None:
        response = self.client.put(
            "/api/model-config",
            json={
                "provider_name": "broken",
                "api_key": "k",
                "models": ["m-1"],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_anthropic_with_responses_api_is_rejected(self) -> None:
        response = self.client.put(
            "/api/model-config",
            json={
                "reasoning_effort": "high",
                "providers": [
                    {
                        "name": "mixed",
                        "base_url": "https://a.test/v1",
                        "api_key": "k",
                        "models": ["m"],
                        "api_format": "anthropic",
                        "use_responses_api": True,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
