"""模型网关失败分类与空响应重试识别的回归测试。"""

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.services.agents.gateway import ModelGatewayUnavailable, _failure_details
from app.services.educoder.executor import model_empty_response, model_schema_error


class ModelProviderError(Exception):
    """模拟 browser_use 的上游错误类型（默认 status_code=502）。"""

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


class FailureClassificationTest(unittest.TestCase):
    def test_missing_tool_calls_is_retryable_empty_response(self):
        error = ModelProviderError("Expected tool_calls in response but got none")

        category, status = _failure_details(error)

        self.assertEqual(category, "empty_response")
        # 客户端默认 status_code=502 不代表真实上游状态，分类以文本为准。
        self.assertEqual(status, 502)

    def test_empty_content_variants_are_classified_as_empty_response(self):
        for text in (
            "model returned no content",
            "empty response from upstream",
            "no message content in result",
        ):
            category, _ = _failure_details(ModelProviderError(text))
            self.assertEqual(category, "empty_response", text)

    def test_existing_categories_are_unchanged(self):
        self.assertEqual(_failure_details(TimeoutError("request timed out"))[0], "timeout")

        rate_limit = ModelProviderError("rate limit exceeded")
        rate_limit.status_code = 429
        self.assertEqual(_failure_details(rate_limit)[0], "rate_limit")

        self.assertEqual(
            _failure_details(ConnectionError("connection reset by peer"))[0], "network"
        )

    def test_empty_response_does_not_turn_every_error_retryable(self):
        self.assertEqual(_failure_details(ValueError("bad request payload"))[0], "unknown")
        self.assertNotEqual(_failure_details(ValueError("invalid json"))[0], "empty_response")

    def test_upstream_502_stays_upstream_5xx(self):
        error = ModelProviderError("Error code: 502 - Internal Server Error")

        self.assertEqual(_failure_details(error)[0], "upstream_5xx")

    def test_missing_tool_calls_wins_over_default_status_code(self):
        # browser_use 的 ModelProviderError 默认 status_code=502，解析失败必须按文本判定。
        error = ModelProviderError("Expected tool_calls in response but got none")

        self.assertEqual(_failure_details(error)[0], "empty_response")

    def test_model_empty_response_follows_exception_chain(self):
        cause = ModelProviderError("Expected tool_calls in response but got none")
        wrapped = ModelGatewayUnavailable("模型角色 code 暂时没有可用上游")
        wrapped.__cause__ = cause

        self.assertTrue(model_empty_response(wrapped))
        self.assertFalse(model_schema_error(wrapped))

    def test_model_empty_response_ignores_unrelated_errors(self):
        self.assertFalse(model_empty_response(ModelProviderError("模型角色 code 暂时没有可用上游")))
        self.assertFalse(model_empty_response(TimeoutError("timed out")))


class _Provider:
    id = "stub-provider"
    name = "Stub"
    cooldown_seconds = 30.0
    max_concurrency = 2
    requests_per_minute = 60
    queue_limit = 30
    tokens_per_minute = 0


class _Profile:
    id = "stub-profile"
    model = "stub-model"


class _Resolved:
    provider = _Provider()
    profile = _Profile()


class _Database:
    def __init__(self):
        self.failures = []
        self.successes = 0

    def claim_model_provider(self, provider_id, probe_ttl_seconds=45.0):
        return True, "closed", None

    def record_model_provider_failure(
        self, provider_id, category, threshold=3, base_cooldown=30.0, max_concurrency=1
    ):
        self.failures.append(category)
        return {"state": "closed", "opened_until": None}

    def record_model_provider_success(
        self, provider_id, max_concurrency=1, cooldown_seconds=30.0
    ):
        self.successes += 1

    def renew_resource_leases(self, attempt_id, ttl_seconds=90.0):
        return None

    def get_model_provider_health(
        self, provider_id, default_concurrency=1, cooldown_seconds=30.0
    ):
        return {"state": "closed", "adaptive_concurrency": default_concurrency}

    def model_provider_recent_usage(self, provider_id, since=0):
        return {"requests": 0, "estimated_tokens": 0, "oldest": None}

    def acquire_resource_leases(self, owner_id, owner_kind, resource_keys, ttl_seconds=90):
        return True, []

    def release_resource_leases(self, owner_id, owner_kind=None, resource_keys=None):
        return None


class _Gateway:
    def __init__(self, database):
        self.database = database

    def resolve(self, role, candidate_index=0):
        return _Resolved()

    def route_size(self, role):
        return 1

    def api_key(self, resolved):
        return "stub-key"

    async def invoke_once(self, resolved, delegate, messages, **kwargs):
        return await delegate()


class RouteRetryTest(unittest.TestCase):
    def test_route_retries_then_succeeds_after_empty_response(self):
        from app.services.agents.config import ModelRole
        from app.services.agents.gateway import GatewayRouteChatModel

        database = _Database()
        gateway = _Gateway(database)
        calls = {"count": 0}

        async def delegate():
            calls["count"] += 1
            if calls["count"] < 2:
                raise ModelProviderError("Expected tool_calls in response but got none")
            return "ok"

        async def scenario():
            route = GatewayRouteChatModel(
                gateway,
                role=ModelRole.CODE,
                attempt_id="attempt-1",
                delegate_builder=lambda resolved, key: delegate,
                max_wait_seconds=10.0,
            )
            return await route.ainvoke([])

        result = asyncio.run(scenario())

        self.assertEqual(result, "ok")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(database.failures, ["empty_response"])
        self.assertEqual(database.successes, 1)


if __name__ == "__main__":
    unittest.main()
