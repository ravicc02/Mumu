from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import main
from app.config import Settings
from app.db import Database


class AgentRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        self.account = self.database.create_account(
            "Fixture",
            "chaoxing",
            self.settings,
        )
        self.database.update_account(self.account["id"], status="ready")

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_channel_user_role_scope_and_account_validation(self):
        with patch.object(main, "database", self.database):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app),
                base_url="http://test",
            ) as client:
                saved = await client.put(
                    "/api/agents/channel-users/user-one",
                    json={
                        "role": "operator",
                        "account_scope": [self.account["id"]],
                        "readonly_account_ids": [self.account["id"]],
                        "enabled": True,
                        "display_name": "Fixture User",
                    },
                )
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()["role"], "operator")
                self.assertTrue(saved.json()["enabled"])

                users = (await client.get(
                    "/api/agents/channel-users?channel=weixin"
                )).json()
                self.assertEqual(len(users), 1)
                self.assertEqual(users[0]["external_user_id"], "user-one")

                invalid = await client.put(
                    "/api/agents/channel-users/user-two",
                    json={
                        "role": "viewer",
                        "account_scope": ["missing"],
                        "enabled": True,
                    },
                )
                self.assertEqual(invalid.status_code, 400)

                deleted = await client.delete(
                    "/api/agents/channel-users/user-one"
                )
                self.assertEqual(deleted.status_code, 200)
                self.assertTrue(deleted.json()["deleted"])

    async def test_connector_reports_channel_state_without_credentials(self):
        self.database.set_setting(
            "weixin_binding",
            {
                "token": "private-token",
                "user_id": "private-user",
                "context_token": "private-context",
            },
        )
        with patch.object(main, "database", self.database):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app),
                base_url="http://test",
            ) as client:
                response = await client.get("/api/connectors/weixin")

        payload = response.json()
        self.assertTrue(payload["configured"])
        self.assertIn("state", payload)
        self.assertNotIn("private-token", str(payload))
        self.assertNotIn("private-context", str(payload))

    async def test_weixin_channel_uses_the_active_database_provider(self):
        with patch.object(main, "database", self.database):
            self.assertIs(main.weixin_channel.database, self.database)


if __name__ == "__main__":
    unittest.main()
