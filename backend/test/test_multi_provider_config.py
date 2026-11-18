from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.services.agents.config import (
    build_multi_provider_config,
    load_agent_model_config,
    save_multi_provider_config,
)


class MultiProviderBuildTests(unittest.TestCase):
    def test_provider_order_defines_fallback_chain(self):
        # 回退顺序：先供应商内模型顺序，再按供应商列表顺序换供应商。
        config = build_multi_provider_config(
            providers=[
                {"name": "primary", "base_url": "https://a.test", "models": ["m-a", "m-b"]},
                {"name": "backup", "base_url": "https://b.test", "models": ["m-c"]},
            ],
            reasoning_effort="high",
        )
        self.assertEqual([item.id for item in config.providers], ["primary", "backup"])
        self.assertEqual([item.priority for item in config.providers], [0, 1])
        route = config.route("structured_answer")
        self.assertEqual(route.models, ["primary/m-a", "primary/m-b", "backup/m-c"])
        self.assertTrue(route.allow_cross_provider)

    def test_api_format_selects_protocol(self):
        config = build_multi_provider_config(
            providers=[
                {
                    "name": "openai-responses",
                    "base_url": "https://o.test",
                    "models": ["m1"],
                    "use_responses_api": True,
                },
                {
                    "name": "anthropic",
                    "base_url": "https://a.test",
                    "models": ["m2"],
                    "api_format": "anthropic",
                },
                {"name": "plain", "base_url": "https://p.test", "models": ["m3"]},
            ],
            reasoning_effort="high",
        )
        protocols = {item.model: item.protocol for item in config.models}
        self.assertEqual(protocols["m1"], "openai_responses")
        self.assertEqual(protocols["m2"], "anthropic_messages")
        self.assertEqual(protocols["m3"], "openai_chat")

    def test_anthropic_rejects_responses_api(self):
        with self.assertRaises(ValueError):
            build_multi_provider_config(
                providers=[
                    {
                        "name": "mixed",
                        "base_url": "https://a.test",
                        "models": ["m"],
                        "api_format": "anthropic",
                        "use_responses_api": True,
                    }
                ],
                reasoning_effort="high",
            )

    def test_provider_without_models_is_rejected(self):
        with self.assertRaises(ValueError):
            build_multi_provider_config(
                providers=[{"name": "empty", "base_url": "https://a.test", "models": []}],
                reasoning_effort="high",
            )

    def test_duplicate_names_receive_unique_ids(self):
        config = build_multi_provider_config(
            providers=[
                {"name": "same", "base_url": "https://a.test", "models": ["m1"]},
                {"name": "same", "base_url": "https://b.test", "models": ["m2"]},
            ],
            reasoning_effort="high",
        )
        self.assertEqual([item.id for item in config.providers], ["same", "same-2"])

    def test_missing_base_url_is_rejected(self):
        with self.assertRaises(ValueError):
            build_multi_provider_config(
                providers=[{"name": "no-url", "base_url": "", "models": ["m"]}],
                reasoning_effort="high",
            )


class MemoryStore:
    def __init__(self):
        self.values: dict[str, str] = {}

    def put(self, reference, value):
        self.values[reference] = value

    def get(self, reference):
        return self.values[reference]


class MultiProviderPersistenceTests(unittest.TestCase):
    def test_each_provider_keeps_its_own_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(data_root=Path(temp_dir), openai_api_key="")
            settings.ensure_directories()
            database = Database(settings)
            database.initialize()
            store = MemoryStore()
            config = save_multi_provider_config(
                database,
                settings,
                providers=[
                    {
                        "name": "primary",
                        "base_url": "https://a.test/v1",
                        "api_key": "key-a",
                        "models": ["m-a"],
                    },
                    {
                        "name": "backup",
                        "base_url": "https://b.test/v1",
                        "api_key": "key-b",
                        "models": ["m-b"],
                        "api_format": "anthropic",
                    },
                ],
                reasoning_effort="high",
                secret_store=store,
            )
            self.assertEqual([item.id for item in config.providers], ["primary", "backup"])
            self.assertEqual(store.values["model-provider/primary"], "key-a")
            self.assertEqual(store.values["model-provider/backup"], "key-b")
            self.assertNotIn("api_key", database.get_setting("model_config"))
            loaded = load_agent_model_config(database, settings, secret_store=store)
            self.assertEqual([item.name for item in loaded.providers], ["primary", "backup"])
            self.assertEqual(loaded.provider("backup").api_format, "anthropic")
            route = loaded.route("assistant")
            self.assertEqual(route.models, ["primary/m-a", "backup/m-b"])

    def test_empty_key_keeps_existing_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(data_root=Path(temp_dir), openai_api_key="")
            settings.ensure_directories()
            database = Database(settings)
            database.initialize()
            store = MemoryStore()
            save_multi_provider_config(
                database,
                settings,
                providers=[
                    {
                        "name": "primary",
                        "base_url": "https://a.test/v1",
                        "api_key": "key-a",
                        "models": ["m-a"],
                    }
                ],
                reasoning_effort="high",
                secret_store=store,
            )
            save_multi_provider_config(
                database,
                settings,
                providers=[
                    {
                        "id": "primary",
                        "name": "primary",
                        "base_url": "https://a.test/v1",
                        "models": ["m-a", "m-b"],
                    }
                ],
                reasoning_effort="low",
                secret_store=store,
            )
            self.assertEqual(store.values["model-provider/primary"], "key-a")
            loaded = load_agent_model_config(database, settings, secret_store=store)
            self.assertEqual(loaded.reasoning_effort, "low")
            self.assertEqual(
                [item.model for item in loaded.models],
                ["m-a", "m-b"],
            )


if __name__ == "__main__":
    unittest.main()
