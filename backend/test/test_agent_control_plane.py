from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from app.config import Settings
from app.db import Database
from app.services.agents.config import (
    DEFAULT_SECRET_REF,
    build_agent_model_config,
    load_agent_model_config,
    resolve_provider_secret,
)
from app.services.agents.contracts import ModelRole
from app.services.agents.gateway import ModelGateway
from app.services.agents.workspace import TaskWorkspace


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, reference: str, value: str) -> None:
        self.values[reference] = value

    def get(self, reference: str) -> str:
        return self.values[reference]

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


class AgentControlPlaneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp_dir.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        self.secrets = MemorySecretStore()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_legacy_model_secret_migrates_out_of_settings(self) -> None:
        self.database.set_setting(
            "model_config",
            {
                "provider_name": "test",
                "base_url": "https://example.test/v1",
                "api_key": "private-model-secret",
                "models": ["primary-model", "fallback-model"],
                "reasoning_effort": "high",
            },
        )

        config = load_agent_model_config(
            self.database,
            self.settings,
            secret_store=self.secrets,
        )

        self.assertIsNotNone(config)
        provider = config.providers[0]
        self.assertEqual(provider.secret_ref, DEFAULT_SECRET_REF)
        self.assertEqual(self.secrets.values[DEFAULT_SECRET_REF], "private-model-secret")
        self.assertEqual(
            resolve_provider_secret(
                self.database,
                self.settings,
                provider,
                secret_store=self.secrets,
            ),
            "private-model-secret",
        )
        self.assertEqual(
            self.database.get_setting("model_config")["models"],
            ["primary-model", "fallback-model"],
        )
        self.assertNotIn("api_key", self.database.get_setting("model_config"))
        canonical = json.dumps(
            self.database.get_setting("agent_model_config"),
            ensure_ascii=False,
        )
        self.assertNotIn("private-model-secret", canonical)

    def test_role_routes_require_declared_vision_capability(self) -> None:
        config = build_agent_model_config(
            provider_name="test",
            base_url="https://example.test/v1",
            models=["text-model", "vision-model"],
            reasoning_effort="high",
            secret_ref=DEFAULT_SECRET_REF,
            capabilities_by_model={
                "text-model": ["tool_calling", "structured_output"],
                "vision-model": ["tool_calling", "structured_output", "vision"],
            },
        )

        desktop_models = [
            config.model(model_id).model
            for model_id in config.route(ModelRole.DESKTOP_VISION).models
        ]
        self.assertEqual(desktop_models, ["vision-model"])

        with self.assertRaises(ValidationError):
            build_agent_model_config(
                provider_name="test",
                base_url="https://example.test/v1",
                models=["text-model"],
                reasoning_effort="high",
                secret_ref=DEFAULT_SECRET_REF,
                capabilities_by_model={
                    "text-model": ["tool_calling", "structured_output"],
                },
                role_models={"desktop_vision": ["text-model"]},
            )

    async def test_gateway_records_real_invocations_without_prompt_content(self) -> None:
        config = build_agent_model_config(
            provider_name="test",
            base_url="https://example.test/v1",
            models=["fixture-model"],
            reasoning_effort="high",
            secret_ref=DEFAULT_SECRET_REF,
        )
        self.secrets.put(DEFAULT_SECRET_REF, "fixture-key")
        self.database.set_setting("agent_model_config", config.model_dump(mode="json"))

        class Usage:
            def model_dump(self, **_kwargs):
                return {"input_tokens": 12, "output_tokens": 3}

        class Delegate:
            model = "fixture-model"
            provider = "test"
            name = "test/fixture-model"

            async def ainvoke(self, messages, output_format=None, **kwargs):
                return SimpleNamespace(usage=Usage())

        gateway = ModelGateway(
            self.database,
            self.settings,
            secret_store=self.secrets,
        )
        llm = gateway.build(
            ModelRole.STRUCTURED_ANSWER,
            attempt_id="attempt-1",
            delegate_builder=lambda _resolved, api_key: Delegate(),
        )

        await llm.ainvoke(
            [{"role": "user", "content": "private prompt text"}],
            output_format=None,
        )

        calls = self.database.list_model_calls(attempt_id="attempt-1")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["status"], "succeeded")
        self.assertEqual(calls[0]["role"], "structured_answer")
        self.assertEqual(calls[0]["usage"]["input_tokens"], 12)
        self.assertNotIn("private prompt text", json.dumps(calls, ensure_ascii=False))
        self.assertEqual(len(calls[0]["request_hash"]), 64)

    def test_checkpoint_and_workspace_manifest_are_persistent(self) -> None:
        workspace = TaskWorkspace(
            Path(self.temp_dir.name),
            "assignment",
            "attempt-42",
            account={"id": "account-1", "platform": "chaoxing"},
            metadata={
                "route": "structured",
                "model_role": "structured_answer",
                "parent_attempt_id": "attempt-41",
                "storage_state_path": "must-not-be-written",
            },
        )
        first = self.database.save_task_checkpoint(
            attempt_id="attempt-42",
            attempt_kind="assignment",
            phase="queued",
            goal={"assignment": "实验一"},
        )
        second = self.database.save_task_checkpoint(
            attempt_id="attempt-42",
            attempt_kind="assignment",
            phase="browser",
            completed={"progress": 10},
        )
        path = workspace.write_checkpoint(second)
        workspace.update(phase="browser", lease_keys=["browser:attempt-42"])

        self.assertEqual(first["version"], 1)
        self.assertEqual(second["version"], 2)
        self.assertEqual(
            self.database.latest_task_checkpoint("attempt-42")["phase"],
            "browser",
        )
        self.assertTrue(path.is_file())
        manifest_text = workspace.manifest_path.read_text(encoding="utf-8")
        self.assertNotIn("storage_state_path", manifest_text)
        self.assertNotIn("must-not-be-written", manifest_text)
        self.assertEqual(
            json.loads(manifest_text)["account"]["platform"],
            "chaoxing",
        )
        self.assertEqual(
            json.loads(manifest_text)["parent_attempt_id"],
            "attempt-41",
        )


if __name__ == "__main__":
    unittest.main()
