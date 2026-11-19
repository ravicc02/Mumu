"""模型供应商协议适配。

底层路由只关心「调用一次模型」；供应商使用 OpenAI Chat、OpenAI Responses
或 Anthropic Messages 的差异集中在这里，避免扩散到网关与业务执行器。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import APIStatusError as AnthropicStatusError
from anthropic import AsyncAnthropic
from anthropic import RateLimitError as AnthropicRateLimitError
from browser_use.llm.base import BaseChatModel
from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError
from browser_use.llm.messages import BaseMessage
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.llm.openai.serializer import OpenAIMessageSerializer
from browser_use.llm.schema import SchemaOptimizer
from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage
from openai import APIConnectionError, APIStatusError, RateLimitError
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return ""


def parse_structured_text(text: str) -> str:
    """模型偶尔会用代码围栏包裹 JSON，解析前先剥离。"""
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _responses_payload(serialized: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    payload: list[dict[str, Any]] = []
    for message in serialized:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role in {"system", "developer"}:
            text = _text_of(content)
            if text:
                instructions.append(text)
            continue
        if role == "tool":
            text = _text_of(content)
            if text:
                payload.append({"role": "user", "content": text})
            continue
        if isinstance(content, str):
            if content:
                payload.append({"role": role, "content": content})
            continue
        parts: list[dict[str, Any]] = []
        text_type = "output_text" if role == "assistant" else "input_text"
        for part in content or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and part.get("text"):
                parts.append({"type": text_type, "text": part["text"]})
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url")
                if url:
                    parts.append({"type": "input_image", "image_url": url})
        if parts:
            payload.append({"role": role if role == "assistant" else "user", "content": parts})
    return "\n\n".join(instructions), payload


def _responses_usage(response: Any) -> ChatInvokeUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    prompt = int(getattr(usage, "input_tokens", 0) or 0)
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    details = getattr(usage, "input_tokens_details", None)
    return ChatInvokeUsage(
        prompt_tokens=prompt,
        prompt_cached_tokens=getattr(details, "cached_tokens", None) if details else None,
        prompt_cache_creation_tokens=None,
        prompt_image_tokens=None,
        completion_tokens=completion,
        total_tokens=int(getattr(usage, "total_tokens", 0) or prompt + completion),
    )


@dataclass
class ResponsesChatOpenAI(ChatOpenAI):
    """强制走 OpenAI Responses（/v1/responses）端点。"""

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        instructions, payload = _responses_payload(
            OpenAIMessageSerializer.serialize_messages(messages)
        )
        params: dict[str, Any] = {"model": self.model, "input": payload}
        if instructions:
            params["instructions"] = instructions
        if self.max_completion_tokens is not None:
            params["max_output_tokens"] = self.max_completion_tokens
        reasoning_models = getattr(self, "reasoning_models", None)
        if reasoning_models and any(
            str(item).lower() in str(self.model).lower() for item in reasoning_models
        ):
            params["reasoning"] = {"effort": self.reasoning_effort}
        elif self.temperature is not None:
            params["temperature"] = self.temperature
        if output_format is not None:
            params["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "agent_output",
                    "strict": True,
                    "schema": SchemaOptimizer.create_optimized_json_schema(output_format),
                }
            }
        try:
            response = await self.get_client().responses.create(**params)
        except RateLimitError as error:
            raise ModelRateLimitError(message=str(error), model=self.name) from error
        except APIConnectionError as error:
            raise ModelProviderError(message=str(error), model=self.name) from error
        except APIStatusError as error:
            raise ModelProviderError(
                message=getattr(error, "message", str(error)),
                status_code=error.status_code,
                model=self.name,
            ) from error
        except Exception as error:  # noqa: BLE001 - 统一成路由可识别的错误
            raise ModelProviderError(message=str(error), model=self.name) from error

        text = str(getattr(response, "output_text", "") or "")
        usage = _responses_usage(response)
        stop_reason = getattr(response, "status", None)
        if output_format is None:
            return ChatInvokeCompletion(completion=text, usage=usage, stop_reason=stop_reason)
        if not text.strip():
            raise ModelProviderError(
                message="Responses API 返回了空内容", status_code=502, model=self.name
            )
        return ChatInvokeCompletion(
            completion=output_format.model_validate_json(parse_structured_text(text)),
            usage=usage,
            stop_reason=stop_reason,
        )


async def _image_source(url: str) -> dict[str, Any] | None:
    value = str(url or "")
    if value.startswith("data:"):
        header, _, data = value.partition(",")
        media = header[5:].split(";")[0] or "image/png"
        return {"type": "base64", "media_type": media, "data": data}
    if value.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(value)
            response.raise_for_status()
            media = response.headers.get("content-type", "image/png").split(";")[0]
            return {
                "type": "base64",
                "media_type": media,
                "data": base64.b64encode(response.content).decode("ascii"),
            }
    return None


async def _anthropic_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks: list[dict[str, Any]] = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and part.get("text"):
            blocks.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            source = await _image_source((part.get("image_url") or {}).get("url") or "")
            if source:
                blocks.append({"type": "image", "source": source})
    return blocks


async def _anthropic_messages(
    serialized: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in serialized:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role in {"system", "developer"}:
            text = _text_of(content)
            if text:
                system_parts.append(text)
            continue
        mapped = "assistant" if role == "assistant" else "user"
        blocks = await _anthropic_blocks(content)
        if not blocks:
            continue
        if converted and converted[-1]["role"] == mapped:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": mapped, "content": blocks})
    if not converted:
        converted = [{"role": "user", "content": [{"type": "text", "text": "继续"}]}]
    return "\n\n".join(system_parts), converted


def _anthropic_usage(response: Any) -> ChatInvokeUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    prompt = (
        int(getattr(usage, "input_tokens", 0) or 0)
        + int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        + int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    return ChatInvokeUsage(
        prompt_tokens=prompt,
        prompt_cached_tokens=getattr(usage, "cache_read_input_tokens", None),
        prompt_cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", None),
        prompt_image_tokens=None,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


@dataclass
class AnthropicMessagesChat(BaseChatModel):
    """Anthropic Messages API（/v1/messages）调用路径。"""

    model: str
    api_key: str
    base_url: str | None = None
    temperature: float | None = 0.2
    max_tokens: int = 8192
    timeout: float = 180.0
    max_retries: int = 0

    @property
    def provider(self) -> str:
        return "anthropic"

    @property
    def name(self) -> str:
        return f"anthropic:{self.model}"

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        system_text, converted = await _anthropic_messages(
            OpenAIMessageSerializer.serialize_messages(messages)
        )
        if output_format is not None:
            schema = json.dumps(
                SchemaOptimizer.create_optimized_json_schema(output_format),
                ensure_ascii=False,
            )
            system_text = (
                f"{system_text}\n\n" if system_text else ""
            ) + f"只返回符合以下 JSON Schema 的 JSON 对象，不要输出解释或代码块：\n{schema}"
        client = AsyncAnthropic(
            api_key=self.api_key,
            base_url=self.base_url or None,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        params: dict[str, Any] = {
            "model": self.model,
            "messages": converted,
            "max_tokens": self.max_tokens,
        }
        if system_text:
            params["system"] = system_text
        if self.temperature is not None:
            params["temperature"] = self.temperature
        try:
            response = await client.messages.create(**params)
        except AnthropicRateLimitError as error:
            raise ModelRateLimitError(message=str(error), model=self.name) from error
        except AnthropicConnectionError as error:
            raise ModelProviderError(message=str(error), model=self.name) from error
        except AnthropicStatusError as error:
            raise ModelProviderError(
                message=str(getattr(error, "message", error)),
                status_code=int(getattr(error, "status_code", 502)),
                model=self.name,
            ) from error
        except Exception as error:  # noqa: BLE001 - 统一成路由可识别的错误
            raise ModelProviderError(message=str(error), model=self.name) from error

        text = "".join(
            str(getattr(block, "text", ""))
            for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", "") == "text"
        )
        usage = _anthropic_usage(response)
        stop_reason = getattr(response, "stop_reason", None)
        if output_format is None:
            return ChatInvokeCompletion(completion=text, usage=usage, stop_reason=stop_reason)
        if not text.strip():
            raise ModelProviderError(
                message="Anthropic 返回了空内容", status_code=502, model=self.name
            )
        return ChatInvokeCompletion(
            completion=output_format.model_validate_json(parse_structured_text(text)),
            usage=usage,
            stop_reason=stop_reason,
        )
