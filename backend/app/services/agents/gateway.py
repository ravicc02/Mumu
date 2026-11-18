from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from .config import load_agent_model_config, resolve_provider_secret
from .contracts import AgentModelConfig, ModelProfile, ModelRole, ProviderProfile
from .quota import ModelQuotaManager


class ModelGatewayError(RuntimeError):
    pass


class ModelGatewayUnavailable(ModelGatewayError):
    pass


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedModel:
    provider: ProviderProfile
    profile: ModelProfile
    candidate_index: int
    profile_role: str
    fallback_from: str | None = None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return repr(value)


def request_fingerprint(messages: Any, output_format: Any = None) -> str:
    payload = {
        "messages": _jsonable(messages),
        "output_format": getattr(output_format, "__qualname__", None),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def estimate_tokens(messages: Any, output_format: Any = None) -> int:
    encoded = json.dumps(
        {
            "messages": _jsonable(messages),
            "output_format": getattr(output_format, "__qualname__", None),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return max(1, math.ceil(len(encoded) / 4) + 1024)


def _failure_details(error: BaseException) -> tuple[str, int | None]:
    status = getattr(error, "status_code", None)
    if not isinstance(status, int):
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        status = None
    name = type(error).__name__.lower()
    text = str(error).lower()
    if status in {401, 403} or "auth" in name:
        category = "auth"
    elif status == 429 or "ratelimit" in name or "rate limit" in text:
        category = "rate_limit"
    elif isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in text:
        category = "timeout"
    elif "connection" in name or "connect" in text or "network" in text:
        category = "network"
    elif (
        "tool_calls" in text
        or "tool calls" in text
        or "returned no content" in text
        or "empty response" in text
        or "no message content" in text
    ):
        # 上游偶发返回纯文本或空内容，未按结构化输出协议给出结果。
        # 客户端包装这类解析失败时会沿用默认的 502 状态码，因此这里必须
        # 先按文本判定，否则会被误当成上游 5xx 并按停止策略处理。
        category = "empty_response"
    elif status is not None and status >= 500:
        category = "upstream_5xx"
    elif "validation" in name or "invalid json" in text or "protocol" in name:
        category = "protocol"
    else:
        category = "unknown"
    return category, status


def _retry_after_seconds(error: BaseException) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


class _ChatModelProtocolCompatibility:
    _verified_api_keys = False

    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type, _handler):
        from pydantic_core import core_schema
        return core_schema.any_schema()


class LedgeredChatModel(_ChatModelProtocolCompatibility):
    """Browser Use compatible model proxy that records every logical call."""

    def __init__(
        self,
        delegate: Any,
        gateway: "ModelGateway",
        resolved: ResolvedModel,
        *,
        attempt_id: str,
    ) -> None:
        self._delegate = delegate
        self._gateway = gateway
        self.resolution = resolved
        self.attempt_id = attempt_id
        self._call_sequence = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def model(self) -> str:
        return self.resolution.profile.model

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def provider(self) -> str:
        return self.resolution.provider.id

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model}"

    async def ainvoke(
        self,
        messages: Any,
        output_format: Any = None,
        **kwargs: Any,
    ) -> Any:
        self._call_sequence += 1
        return await self._gateway.invoke_once(
            self.resolution,
            self._delegate,
            messages,
            output_format=output_format,
            attempt_id=self.attempt_id,
            call_sequence=self._call_sequence,
            kwargs=kwargs,
        )


class GatewayRouteChatModel(_ChatModelProtocolCompatibility):
    """Browser Use compatible model with quota, circuit and route fallback."""

    def __init__(
        self,
        gateway: "ModelGateway",
        *,
        role: ModelRole,
        attempt_id: str,
        delegate_builder: Callable[[ResolvedModel, str], Any],
        waiting_callback: Callable[[str | None, str | None], None] | None = None,
        max_wait_seconds: float = 450,
    ) -> None:
        self._gateway = gateway
        self.role = role
        self.attempt_id = attempt_id
        self.delegate_builder = delegate_builder
        self.waiting_callback = waiting_callback
        self.max_wait_seconds = max(5.0, float(max_wait_seconds))
        self.primary = gateway.resolve(role, 0)
        self.resolution = self.primary
        self._delegates: dict[str, Any] = {}
        self._call_sequence = 0
        self._quota = ModelQuotaManager(
            gateway.database,
            attempt_id=attempt_id,
            role=role,
            waiting_callback=waiting_callback,
        )

    def __getattr__(self, name: str) -> Any:
        delegate = self._delegates.get(self.primary.profile.id)
        if delegate is None:
            delegate = self._delegate_for(self.primary)
        return getattr(delegate, name)

    @property
    def model(self) -> str:
        return self.primary.profile.model

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def provider(self) -> str:
        return self.primary.provider.id

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model}"

    async def ainvoke(
        self,
        messages: Any,
        output_format: Any = None,
        **kwargs: Any,
    ) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.max_wait_seconds
        route_size = self._gateway.route_size(self.role)
        estimated = estimate_tokens(messages, output_format)
        last_error: BaseException | None = None
        while True:
            next_available = deadline
            last_category: str | None = None
            for candidate_index in range(route_size):
                resolved = self._gateway.resolve(self.role, candidate_index)
                allowed, _health_state, available_at = (
                    self._gateway.database.claim_model_provider(
                        resolved.provider.id,
                        probe_ttl_seconds=min(45.0, self.max_wait_seconds),
                    )
                )
                if not allowed:
                    if available_at:
                        next_available = min(next_available, float(available_at))
                    self._notify(
                        f"模型上游 {resolved.provider.name} 正在冷却",
                        (
                            datetime_from_epoch(available_at)
                            if available_at
                            else None
                        ),
                    )
                    continue
                try:
                    lease = await self._quota.acquire(
                        resolved.provider,
                        estimated_tokens=estimated,
                        deadline=deadline,
                    )
                except TimeoutError as error:
                    last_error = error
                    last_category = "timeout"
                    next_available = min(next_available, deadline)
                    continue
                try:
                    delegate = self._delegate_for(resolved)
                    safe_retry = 0
                    empty_response_retries = 0
                    while True:
                        self._call_sequence += 1
                        try:
                            result = await self._gateway.invoke_once(
                                resolved,
                                delegate,
                                messages,
                                output_format=output_format,
                                attempt_id=self.attempt_id,
                                call_sequence=self._call_sequence,
                                estimated_tokens=estimated,
                                kwargs=kwargs,
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as error:
                            last_error = error
                            category, _ = _failure_details(error)
                            last_category = category
                            health = self._gateway.database.record_model_provider_failure(
                                resolved.provider.id,
                                category,
                                threshold=1 if category == "auth" else 3,
                                base_cooldown=resolved.provider.cooldown_seconds,
                                max_concurrency=resolved.provider.max_concurrency,
                            )
                            if health.get("state") == "open":
                                next_available = min(
                                    next_available,
                                    float(health.get("opened_until") or time.time() + 5),
                                )
                            if (
                                category == "empty_response"
                                and empty_response_retries < 2
                                and health.get("state") != "open"
                            ):
                                empty_response_retries += 1
                                self._notify(
                                    f"模型上游返回空响应，{resolved.provider.name} 正在重新请求",
                                    datetime_from_epoch(time.time() + 0.5),
                                )
                                self._gateway.database.renew_resource_leases(
                                    self.attempt_id,
                                    ttl_seconds=90.0,
                                )
                                await asyncio.sleep(0.5)
                                continue
                            if (
                                category in {"rate_limit", "timeout", "network", "upstream_5xx"}
                                and safe_retry < (3 if category == "upstream_5xx" else 1)
                                and health.get("state") != "open"
                            ):
                                safe_retry += 1
                                delay = _retry_after_seconds(error) or 1.0
                                self._notify(
                                    f"模型上游暂时不可用，{resolved.provider.name} 正在重试",
                                    datetime_from_epoch(time.time() + delay),
                                )
                                self._gateway.database.renew_resource_leases(
                                    self.attempt_id,
                                    ttl_seconds=max(90.0, delay + 30),
                                )
                                await asyncio.sleep(min(delay, max(0.0, deadline - loop.time())))
                                continue
                            break
                        else:
                            self._gateway.database.record_model_provider_success(
                                resolved.provider.id,
                                max_concurrency=resolved.provider.max_concurrency,
                                cooldown_seconds=resolved.provider.cooldown_seconds,
                            )
                            self._notify(None, None)
                            return result
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    last_error = error
                    last_category, _ = _failure_details(error)
                    self._notify(None, None)
                finally:
                    lease.release()
                    self._quota.release_task_lock()
            remaining = deadline - loop.time()
            if (
                remaining <= 0
                or last_category in {"auth", "protocol", "unknown", "empty_response"}
            ):
                break
            if next_available > 1_000_000_000:
                wait_seconds = max(0.25, next_available - time.time())
            else:
                wait_seconds = max(0.25, next_available - loop.time())
            wait_seconds = min(5.0, wait_seconds, remaining)
            self._notify(
                "等待可用模型上游",
                datetime_from_epoch(time.time() + wait_seconds),
            )
            await asyncio.sleep(wait_seconds)
        if last_error:
            raise ModelGatewayUnavailable(
                f"模型角色 {self.role.value} 暂时没有可用上游"
            ) from last_error
        raise ModelGatewayUnavailable(f"模型角色 {self.role.value} 暂时没有可用上游")

    def _delegate_for(self, resolved: ResolvedModel) -> Any:
        delegate = self._delegates.get(resolved.profile.id)
        if delegate is not None:
            return delegate
        key = self._gateway.api_key(resolved)
        if not key:
            raise ModelGatewayError("当前模型服务缺少可用密钥")
        delegate = self.delegate_builder(resolved, key)
        self._delegates[resolved.profile.id] = delegate
        return delegate

    def _notify(self, reason: str | None, retry_at: str | None) -> None:
        if self.waiting_callback:
            try:
                self.waiting_callback(reason, retry_at)
            except Exception:
                logger.exception("Model route waiting callback failed")


def datetime_from_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


class ModelGateway:
    def __init__(self, database, settings, *, secret_store=None) -> None:
        self.database = database
        self.settings = settings
        self.secret_store = secret_store
        self.config = load_agent_model_config(
            database,
            settings,
            secret_store=secret_store,
        )

    def _require_config(self) -> AgentModelConfig:
        if self.config is None:
            raise ModelGatewayError("尚未配置可用模型")
        return self.config

    def resolve(self, role: ModelRole | str, candidate_index: int = 0) -> ResolvedModel:
        config = self._require_config()
        normalized = ModelRole(role)
        try:
            route = config.route(normalized)
        except KeyError as error:
            raise ModelGatewayError(f"模型角色 {normalized.value} 尚无可用路由") from error
        if not 0 <= candidate_index < len(route.models):
            raise ModelGatewayError(
                f"模型角色 {normalized.value} 没有第 {candidate_index + 1} 个兼容模型"
            )
        profile = config.model(route.models[candidate_index])
        provider = config.provider(profile.provider_id)
        fallback_from = (
            config.model(route.models[candidate_index - 1]).model
            if candidate_index > 0
            else None
        )
        return ResolvedModel(
            provider=provider,
            profile=profile,
            candidate_index=candidate_index,
            fallback_from=fallback_from,
            profile_role=normalized.value,
        )

    def route_size(self, role: ModelRole | str) -> int:
        config = self._require_config()
        try:
            return len(config.route(role).models)
        except KeyError:
            return 0

    def api_key(self, resolved: ResolvedModel) -> str:
        return resolve_provider_secret(
            self.database,
            self.settings,
            resolved.provider,
            secret_store=self.secret_store,
        )

    async def invoke_once(
        self,
        resolved: ResolvedModel,
        delegate: Any,
        messages: Any,
        *,
        output_format: Any,
        attempt_id: str,
        call_sequence: int,
        estimated_tokens: int | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        fingerprint = request_fingerprint(messages, output_format)
        record = self.database.begin_model_call(
            attempt_id=attempt_id,
            role=resolved.profile_role,
            provider_id=resolved.provider.id,
            model=resolved.profile.model,
            request_hash=fingerprint,
            candidate_index=resolved.candidate_index,
            fallback_from=resolved.fallback_from,
            idempotency_key=hashlib.sha256(
                (
                    f"{attempt_id}|{resolved.profile.id}|"
                    f"{call_sequence}|{fingerprint}"
                ).encode("utf-8")
            ).hexdigest(),
            estimated_tokens=(
                estimated_tokens
                if estimated_tokens is not None
                else estimate_tokens(messages, output_format)
            ),
        )
        started = time.perf_counter()
        try:
            result = await delegate.ainvoke(
                messages,
                output_format=output_format,
                **(kwargs or {}),
            )
        except asyncio.CancelledError:
            self._finish_model_call(
                record["id"],
                status="cancelled",
                latency_ms=int((time.perf_counter() - started) * 1000),
                failure_class="cancelled",
            )
            raise
        except Exception as error:
            category, status = _failure_details(error)
            self._finish_model_call(
                record["id"],
                status="error",
                latency_ms=int((time.perf_counter() - started) * 1000),
                failure_class=category,
                http_status=status,
            )
            raise
        usage = getattr(result, "usage", None)
        self._finish_model_call(
            record["id"],
            status="succeeded",
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=(
                usage.model_dump(mode="json")
                if hasattr(usage, "model_dump")
                else _jsonable(usage)
            ),
            failure_class=None,
            http_status=None,
        )
        return result

    def _finish_model_call(self, call_id: str, **fields: Any) -> None:
        try:
            self.database.finish_model_call(call_id, **fields)
        except Exception:
            logger.exception("Model call ledger finalization failed")

    @staticmethod
    async def probe_completion(
        database,
        *,
        role: ModelRole | str,
        attempt_id: str,
        provider_id: str,
        model: str,
        base_url: str,
        api_key: str,
        messages: list[dict[str, Any]],
        timeout: float = 45,
        api_format: str = "openai",
        use_responses_api: bool = False,
    ) -> dict[str, Any]:
        normalized_role = ModelRole(role)
        fingerprint = request_fingerprint(messages)
        record = database.begin_model_call(
            attempt_id=attempt_id,
            role=normalized_role.value,
            provider_id=provider_id,
            model=model,
            request_hash=fingerprint,
            idempotency_key=hashlib.sha256(
                f"{attempt_id}|{provider_id}|{model}|{fingerprint}".encode("utf-8")
            ).hexdigest(),
        )
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if api_format == "anthropic":
                    response = await client.post(
                        f"{base_url.rstrip('/')}/messages",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                        },
                        json={
                            "model": model,
                            "max_tokens": 8,
                            "messages": messages,
                        },
                    )
                elif use_responses_api:
                    response = await client.post(
                        f"{base_url.rstrip('/')}/responses",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": model,
                            "input": messages,
                            "max_output_tokens": 16,
                        },
                    )
                else:
                    response = await client.post(
                        f"{base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": model,
                            "messages": messages,
                            "max_tokens": 8,
                        },
                    )
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
        except Exception as error:
            category, status = _failure_details(error)
            try:
                database.finish_model_call(
                    record["id"],
                    status="error",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    failure_class=category,
                    http_status=status,
                )
            except Exception:
                logger.exception("Model probe ledger finalization failed")
            raise
        try:
            database.finish_model_call(
                record["id"],
                status="succeeded",
                latency_ms=int((time.perf_counter() - started) * 1000),
                usage=payload.get("usage") if isinstance(payload, dict) else None,
            )
        except Exception:
            logger.exception("Model probe ledger finalization failed")
        return payload if isinstance(payload, dict) else {}

    def build(
        self,
        role: ModelRole | str,
        *,
        attempt_id: str,
        delegate_builder: Callable[[ResolvedModel, str], Any],
        candidate_index: int = 0,
    ) -> LedgeredChatModel:
        resolved = self.resolve(role, candidate_index)
        key = self.api_key(resolved)
        if not key:
            raise ModelGatewayError("当前模型服务缺少可用密钥")
        # The role is kept on the resolved object for the ledger without
        # coupling the provider contract to a single task type.
        delegate = delegate_builder(resolved, key)
        return LedgeredChatModel(
            delegate,
            self,
            resolved,
            attempt_id=attempt_id,
        )

    def build_route(
        self,
        role: ModelRole | str,
        *,
        attempt_id: str,
        delegate_builder: Callable[[ResolvedModel, str], Any],
        waiting_callback: Callable[[str | None, str | None], None] | None = None,
        max_wait_seconds: float = 450,
    ) -> GatewayRouteChatModel:
        normalized = ModelRole(role)
        if self.route_size(normalized) <= 0:
            raise ModelGatewayError(f"模型角色 {normalized.value} 尚无可用路由")
        return GatewayRouteChatModel(
            self,
            role=normalized,
            attempt_id=attempt_id,
            delegate_builder=delegate_builder,
            waiting_callback=waiting_callback,
            max_wait_seconds=max_wait_seconds,
        )
