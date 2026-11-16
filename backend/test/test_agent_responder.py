from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.config import Settings
from app.db import Database
from app.services.agents.config import build_agent_model_config
from app.services.agents.gateway import ModelGateway
from app.services.agents.responder import AssistantDecision, GatewayAssistantResponder


class AssistantResponderTests(unittest.IsolatedAsyncioTestCase):
    async def test_assistant_route_uses_quota_and_records_the_real_invocation(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(data_root=Path(temp))
            settings.ensure_directories()
            database = Database(settings)
            database.initialize()
            config = build_agent_model_config(
                provider_name="Fixture", base_url="https://example.test/v1",
                models=["fixture-model"], reasoning_effort="high", secret_ref="fixture-secret",
            )
            database.set_setting("agent_model_config", config.model_dump(mode="json"))
            gateway = ModelGateway(database, settings, secret_store=SimpleNamespace(
                get=lambda _ref: "fixture-api-key",
            ))
            requests = []

            class Delegate:
                async def ainvoke(self, messages, output_format=None, **kwargs):
                    requests.append((messages, output_format))
                    return SimpleNamespace(
                        completion=AssistantDecision(intent="active_tasks"), usage=None,
                    )

            responder = GatewayAssistantResponder(lambda: database, lambda: settings)
            with patch("app.services.agents.responder.ModelGateway", return_value=gateway), patch.object(
                responder, "_delegate", return_value=Delegate(),
            ):
                decision = await responder({
                    "session_id": "fixture-session", "text": "手头还有什么事在忙？",
                    "context": {"messages": [], "blocks": []},
                })

            self.assertEqual(decision.intent, "active_tasks")
            self.assertEqual(len(requests), 1)
            self.assertIs(requests[0][1], AssistantDecision)
            with database.connect() as connection:
                calls = [dict(row) for row in connection.execute("SELECT * FROM model_calls")]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["role"], "assistant")
            self.assertEqual(calls[0]["status"], "succeeded")
            self.assertNotIn("fixture-api-key", json.dumps(calls))
            self.assertNotIn("手头还有", json.dumps(calls, ensure_ascii=False))
            self.assertFalse(database.list_resource_leases())


if __name__ == "__main__":
    unittest.main()
