from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .contracts import ModelRole, ProviderProfile


@dataclass
class QuotaLease:
    database: object
    owner_id: str
    keys: list[str]
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        self.database.release_resource_leases(
            self.owner_id,
            resource_keys=self.keys,
        )


class ModelQuotaManager:
    def __init__(
        self,
        database,
        *,
        attempt_id: str,
        role: ModelRole,
        waiting_callback: Callable[[str | None, str | None], None] | None = None,
    ) -> None:
        self.database = database
        self.attempt_id = attempt_id
        self.role = role
        self.waiting_callback = waiting_callback
        self.task_lock = asyncio.Lock()

    async def acquire(
        self,
        provider: ProviderProfile,
        *,
        estimated_tokens: int,
        deadline: float,
    ) -> QuotaLease:
        await self.task_lock.acquire()
        try:
            while True:
                health = self.database.get_model_provider_health(
                    provider.id,
                    default_concurrency=provider.max_concurrency,
                    cooldown_seconds=provider.cooldown_seconds,
                )
                effective = max(
                    1,
                    min(
                        provider.max_concurrency,
                        int(health.get("adaptive_concurrency") or provider.max_concurrency),
                    ),
                )
                if self.role != ModelRole.ASSISTANT and effective > 1:
                    effective = max(1, effective - 1)
                current = time.time()
                usage = self.database.model_provider_recent_usage(
                    provider.id,
                    since=current - 60,
                )
                oldest = _timestamp(usage.get("oldest"))
                request_limit = min(
                    provider.requests_per_minute,
                    provider.queue_limit,
                )
                request_wait = (
                    max(0.0, oldest + 60 - current)
                    if int(usage.get("requests") or 0) >= request_limit
                    else 0.0
                )
                token_wait = (
                    max(0.0, oldest + 60 - current)
                    if provider.tokens_per_minute > 0
                    and int(usage.get("requests") or 0) > 0
                    and int(usage.get("estimated_tokens") or 0)
                    + max(1, int(estimated_tokens))
                    > provider.tokens_per_minute
                    else 0.0
                )
                wait_seconds = max(request_wait, token_wait)
                if wait_seconds > 0:
                    await self._wait(
                        min(wait_seconds, max(0.1, deadline - asyncio.get_running_loop().time())),
                        "模型上游 RPM/TPM 限额已满",
                    )
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError("等待模型上游配额超时")
                    continue
                for slot in range(effective):
                    key = f"model-slot:{provider.id}:{slot}"
                    acquired, _ = self.database.acquire_resource_leases(
                        owner_id=self.attempt_id,
                        owner_kind="model",
                        resource_keys=[key],
                        ttl_seconds=90,
                    )
                    if acquired:
                        self._notify(None, None)
                        return QuotaLease(
                            database=self.database,
                            owner_id=self.attempt_id,
                            keys=[key],
                        )
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("等待模型上游并发槽位超时")
                await self._wait(min(0.5, remaining), "等待模型上游并发槽位")
        except BaseException:
            self.task_lock.release()
            raise

    async def _wait(self, seconds: float, reason: str) -> None:
        retry_at = (
            datetime.now(timezone.utc).timestamp() + max(0.0, seconds)
        )
        self._notify(
            reason,
            datetime.fromtimestamp(retry_at, timezone.utc).isoformat(),
        )
        await asyncio.sleep(max(0.05, min(seconds, 5.0)))

    def release_task_lock(self) -> None:
        if self.task_lock.locked():
            self.task_lock.release()

    def _notify(self, reason: str | None, retry_at: str | None) -> None:
        if self.waiting_callback:
            try:
                self.waiting_callback(reason, retry_at)
            except Exception:
                pass


def _timestamp(value: str | None) -> float:
    if not value:
        return time.time()
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return time.time()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
