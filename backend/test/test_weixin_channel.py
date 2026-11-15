from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.config import Settings
from app.db import Database
from app.services.notifications import weixin_channel


class FakeAssistant:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def handle_message(self, **kwargs):
        self.messages.append(kwargs)
        return {
            "status": "ok",
            "session_id": f"session-{len(self.messages)}",
            "queued": True,
        }


class WeixinChannelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        self.database.set_setting(
            "weixin_binding",
            {
                "token": "fixture-token",
                "bot_id": "fixture-bot",
                "user_id": "bound-user",
                "base": "https://fixture.test",
                "context_token": "",
                "get_updates_buf": "cursor-1",
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_ingest_persists_messages_cursor_and_per_user_contexts(self):
        payload = {
            "ret": 0,
            "get_updates_buf": "cursor-2",
            "msgs": [
                {
                    "message_id": "message-a",
                    "from_user_id": "bound-user",
                    "context_token": "context-a",
                    "message_type": 2,
                    "item_list": [
                        {"type": 1, "text_item": {"text": "查询总况"}}
                    ],
                },
                {
                    "message_id": "message-b",
                    "from_user_id": "other-user",
                    "context_token": "context-b",
                    "message_type": 2,
                    "text": "你好",
                },
            ],
        }

        inserted = weixin_channel.ingest_updates(self.database, payload)
        duplicate = weixin_channel.ingest_updates(self.database, payload)

        self.assertEqual([item["external_message_id"] for item in inserted], [
            "message-a",
            "message-b",
        ])
        self.assertEqual(duplicate, [])
        binding = self.database.get_setting("weixin_binding")
        self.assertEqual(binding["get_updates_buf"], "cursor-2")
        self.assertEqual(binding["context_token"], "context-a")
        self.assertEqual(
            weixin_channel.get_context(self.database, "bound-user"),
            "context-a",
        )
        self.assertEqual(
            weixin_channel.get_context(self.database, "other-user"),
            "context-b",
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT content_json FROM channel_messages ORDER BY rowid"
            ).fetchall()
        self.assertNotIn("context-a", json.dumps([dict(row) for row in rows]))
        self.assertNotIn("context-b", json.dumps([dict(row) for row in rows]))

    async def test_pending_messages_are_processed_once_and_unsupported_items_reply(self):
        fake = FakeAssistant()
        channel = weixin_channel.WeixinChannel(self.database, fake)
        weixin_channel.ingest_updates(
            self.database,
            {
                "get_updates_buf": "cursor-text",
                "msgs": [{
                    "message_id": "text-message",
                    "from_user_id": "bound-user",
                    "context_token": "context-text",
                    "message_type": 2,
                    "text": "任务总况",
                }],
            },
        )

        self.assertEqual(await channel.process_pending(), 1)
        self.assertEqual(await channel.process_pending(), 0)
        self.assertEqual(fake.messages[0]["text"], "任务总况")
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT processed_at, session_id FROM channel_messages
                WHERE external_message_id = 'text-message'
                """
            ).fetchone()
        self.assertTrue(row["processed_at"])
        self.assertEqual(row["session_id"], "session-1")

        weixin_channel.ingest_updates(
            self.database,
            {
                "get_updates_buf": "cursor-file",
                "msgs": [{
                    "message_id": "file-message",
                    "from_user_id": "bound-user",
                    "context_token": "context-file",
                    "message_type": 7,
                    "item_list": [{"type": 5, "file_item": {"name": "a.pdf"}}],
                }],
            },
        )
        self.assertEqual(await channel.process_pending(), 1)
        outbox = channel.outbox.list_messages(state="pending")
        self.assertEqual(len(outbox), 1)
        self.assertIn("只支持文字消息", outbox[0]["payload"]["text"])

    async def test_outbox_send_uses_bound_user_context_and_mumu_identity(self):
        context = {
            "message_id": "context-message",
            "from_user_id": "bound-user",
            "context_token": "fresh-context",
            "message_type": 2,
            "text": "hello",
        }
        weixin_channel.ingest_updates(
            self.database,
            {"get_updates_buf": "cursor-context", "msgs": [context]},
        )
        response = httpx.Response(
            200,
            json={"ret": 0},
            request=httpx.Request(
                "POST",
                "https://fixture.test/ilink/bot/sendmessage",
            ),
        )
        client = AsyncMock(return_value=response)
        manager = AsyncMock()
        manager.__aenter__.return_value = type(
            "Client",
            (),
            {"post": client},
        )()
        with patch(
            "app.services.notifications.weixin_channel.httpx.AsyncClient",
            return_value=manager,
        ):
            await weixin_channel.send_outbox_message(
                self.database,
                {
                    "channel": "weixin",
                    "external_user_id": "bound-user",
                    "payload": {
                        "kind": "assistant_reply",
                        "text": "任务总况已更新",
                    },
                },
            )

        body = client.call_args.kwargs["json"]
        self.assertEqual(body["msg"]["context_token"], "fresh-context")
        self.assertEqual(
            body["base_info"]["bot_agent"].split("/", 1)[0],
            "Mumu",
        )
        self.assertEqual(self.database.get_setting("weixin_delivery")["error"], None)

    async def test_auth_failure_clears_old_token_cursor_and_contexts(self):
        weixin_channel.ingest_updates(
            self.database,
            {
                "get_updates_buf": "cursor-old",
                "msgs": [{
                    "message_id": "context-old",
                    "from_user_id": "bound-user",
                    "context_token": "context-old",
                    "message_type": 2,
                    "text": "hello",
                }],
            },
        )
        response = httpx.Response(
            200,
            json={
                "ret": -2,
                "errmsg": "context token expired",
                "request_id": "request-auth",
            },
            request=httpx.Request(
                "POST",
                "https://fixture.test/ilink/bot/getupdates",
            ),
        )
        manager = AsyncMock()
        manager.__aenter__.return_value = type(
            "Client",
            (),
            {"post": AsyncMock(return_value=response)},
        )()
        with patch(
            "app.services.notifications.weixin_channel.httpx.AsyncClient",
            return_value=manager,
        ):
            with self.assertRaises(RuntimeError):
                await weixin_channel.fetch_and_ingest(self.database)

        binding = self.database.get_setting("weixin_binding")
        self.assertEqual(binding["token"], "")
        self.assertEqual(binding["get_updates_buf"], "")
        self.assertEqual(binding["context_token"], "")
        self.assertEqual(
            weixin_channel.channel_snapshot(self.database)["state"],
            "auth_required",
        )
        self.assertEqual(
            weixin_channel.get_context(self.database, "bound-user"),
            "",
        )

    async def test_http_unauthorized_clears_binding_and_requires_reauthorization(self):
        weixin_channel.ingest_updates(
            self.database,
            {
                "get_updates_buf": "cursor-http",
                "msgs": [{
                    "message_id": "context-http",
                    "from_user_id": "bound-user",
                    "context_token": "context-http",
                    "message_type": 2,
                    "text": "hello",
                }],
            },
        )
        response = httpx.Response(
            401,
            json={"ret": 0},
            request=httpx.Request(
                "POST",
                "https://fixture.test/ilink/bot/getupdates",
            ),
        )
        manager = AsyncMock()
        manager.__aenter__.return_value = type(
            "Client",
            (),
            {"post": AsyncMock(return_value=response)},
        )()
        with patch(
            "app.services.notifications.weixin_channel.httpx.AsyncClient",
            return_value=manager,
        ):
            with self.assertRaises(httpx.HTTPStatusError):
                await weixin_channel.fetch_and_ingest(self.database)

        binding = self.database.get_setting("weixin_binding")
        self.assertEqual(binding["token"], "")
        self.assertEqual(binding["get_updates_buf"], "")
        self.assertEqual(
            weixin_channel.channel_snapshot(self.database)["state"],
            "auth_required",
        )

    async def test_successful_poll_clears_a_stale_delivery_error(self):
        self.database.set_setting(
            "weixin_delivery",
            {"error": "previous rejection", "last_failed_at": "before"},
        )
        response = httpx.Response(
            200,
            json={
                "ret": 0,
                "get_updates_buf": "cursor-recovered",
                "msgs": [{
                    "message_id": "recovered-message",
                    "from_user_id": "bound-user",
                    "context_token": "recovered-context",
                    "message_type": 2,
                    "text": "hello",
                }],
            },
            request=httpx.Request(
                "POST",
                "https://fixture.test/ilink/bot/getupdates",
            ),
        )
        manager = AsyncMock()
        manager.__aenter__.return_value = type(
            "Client",
            (),
            {"post": AsyncMock(return_value=response)},
        )()
        with patch(
            "app.services.notifications.weixin_channel.httpx.AsyncClient",
            return_value=manager,
        ):
            await weixin_channel.fetch_and_ingest(self.database)

        delivery = self.database.get_setting("weixin_delivery")
        self.assertIsNone(delivery["error"])
        self.assertEqual(delivery["last_failed_at"], "before")
        self.assertTrue(delivery["last_poll_recovered_at"])
        self.assertTrue(weixin_channel.channel_snapshot(self.database)["ready"])

    async def test_send_without_user_context_is_not_marked_as_delivered(self):
        with self.assertRaises(weixin_channel.WeixinContextRequired):
            await weixin_channel.send_text_message(
                self.database,
                to_user_id="new-user",
                text="hello",
            )
        self.assertIsNone(self.database.get_setting("weixin_delivery"))


if __name__ == "__main__":
    unittest.main()
