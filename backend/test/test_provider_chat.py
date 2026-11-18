from __future__ import annotations

import asyncio
import unittest

from browser_use.llm.messages import AssistantMessage, SystemMessage, UserMessage
from browser_use.llm.openai.serializer import OpenAIMessageSerializer

from app.services.agents.provider_chat import (
    _anthropic_messages,
    _responses_payload,
    parse_structured_text,
)


def serialize(messages):
    return OpenAIMessageSerializer.serialize_messages(messages)


class ResponsesPayloadTests(unittest.TestCase):
    def test_system_message_becomes_instructions(self):
        instructions, payload = _responses_payload(
            serialize([SystemMessage(content="规则"), UserMessage(content="你好")])
        )
        self.assertEqual(instructions, "规则")
        self.assertEqual(payload, [{"role": "user", "content": "你好"}])

    def test_multiple_system_messages_are_joined(self):
        instructions, _ = _responses_payload(
            serialize([SystemMessage(content="一"), SystemMessage(content="二")])
        )
        self.assertEqual(instructions, "一\n\n二")

    def test_image_parts_become_input_image(self):
        _, payload = _responses_payload(
            serialize([
                UserMessage(content=[
                    {"type": "text", "text": "看看"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ])
            ])
        )
        parts = payload[0]["content"]
        self.assertEqual(parts[0], {"type": "input_text", "text": "看看"})
        self.assertEqual(parts[1]["type"], "input_image")
        self.assertTrue(parts[1]["image_url"].startswith("data:image/png"))


class AnthropicMessagesTests(unittest.TestCase):
    def test_consecutive_same_role_is_merged(self):
        system, messages = asyncio.run(
            _anthropic_messages(serialize([UserMessage(content="a"), UserMessage(content="b")]))
        )
        self.assertEqual(system, "")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual([block["text"] for block in messages[0]["content"]], ["a", "b"])

    def test_system_is_split_and_roles_preserved(self):
        system, messages = asyncio.run(
            _anthropic_messages(
                serialize([
                    SystemMessage(content="规则"),
                    UserMessage(content="问题"),
                    AssistantMessage(content="回答"),
                ])
            )
        )
        self.assertEqual(system, "规则")
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])

    def test_data_uri_image_becomes_base64_source(self):
        _, messages = asyncio.run(
            _anthropic_messages(
                serialize([
                    UserMessage(content=[
                        {"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
                    ])
                ])
            )
        )
        blocks = messages[0]["content"]
        self.assertEqual(blocks[0], {"type": "text", "text": "看图"})
        self.assertEqual(blocks[1]["type"], "image")
        self.assertEqual(blocks[1]["source"]["media_type"], "image/jpeg")
        self.assertEqual(blocks[1]["source"]["data"], "QUJD")

    def test_empty_conversation_gets_fallback_user_turn(self):
        _, messages = asyncio.run(_anthropic_messages([]))
        self.assertEqual(messages[0]["role"], "user")
        self.assertTrue(messages[0]["content"])


class StructuredTextTests(unittest.TestCase):
    def test_code_fence_is_stripped(self):
        raw = "```json\n{\"ok\": true}\n```"
        self.assertEqual(parse_structured_text(raw), '{"ok": true}')

    def test_plain_json_is_unchanged(self):
        self.assertEqual(parse_structured_text('{"ok": true}'), '{"ok": true}')


if __name__ == "__main__":
    unittest.main()
