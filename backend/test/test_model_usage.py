from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main
from app.config import Settings
from app.db import Database


class ModelUsageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = Settings(data_root=Path(temp.name), openai_api_key="")
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        patcher = patch.object(main, "database", self.database)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)

    def call(self, usage=None, status="succeeded", model="demo", old=False):
        record = self.database.begin_model_call(attempt_id="test", role="primary", provider_id="demo-provider", model=model, request_hash="private-hash")
        if status != "running":
            self.database.finish_model_call(record["id"], status=status, latency_ms=1200, usage=usage, http_status=429 if status == "error" else None)
        if old:
            with self.database.connect() as connection:
                connection.execute("UPDATE model_calls SET created_at = ? WHERE id = ?", ((datetime.now(timezone.utc) - timedelta(days=10)).isoformat(), record["id"]))
        return record

    def test_aggregates_all_pages_and_preserves_unknown_usage(self):
        for _ in range(1010):
            self.call({"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110, "prompt_tokens_details": {"cached_tokens": 80}})
        self.call(status="error")
        self.call(status="running")
        self.call({"input_tokens": 9999}, old=True)
        result = self.client.get("/api/model-usage?period=24h&limit=2").json()
        self.assertEqual(result["summary"]["requests"], 1012)
        self.assertEqual(result["summary"]["input_tokens"], 101000)
        self.assertEqual(result["summary"]["cached_tokens"], 80800)
        self.assertEqual(result["summary"]["usage_requests"], 1010)
        self.assertEqual(len(result["records"]), 2)
        self.assertIsNone(result["records"][0]["total_tokens"])
        self.assertNotIn("request_hash", str(result))
        self.assertNotIn("usage_json", str(result))

    def test_normalizes_browser_responses_and_anthropic_usage(self):
        self.call({"prompt_tokens": 100, "completion_tokens": 20, "prompt_cached_tokens": 60, "prompt_cache_creation_tokens": 10})
        self.call({"input_tokens": 100, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 50}})
        self.call({"input_tokens": 30, "output_tokens": 20, "cache_read_input_tokens": 60, "cache_creation_input_tokens": 10})
        result = self.client.get("/api/model-usage?period=all").json()
        self.assertEqual(result["summary"]["input_tokens"], 300)
        self.assertEqual(result["summary"]["output_tokens"], 60)
        self.assertEqual(result["summary"]["total_tokens"], 360)
        self.assertEqual(result["summary"]["cached_tokens"], 170)
        self.assertEqual(result["summary"]["cache_creation_tokens"], 20)

    def test_anthropic_adapter_includes_cached_input(self):
        from types import SimpleNamespace
        from app.services.agents.provider_chat import _anthropic_usage
        usage = _anthropic_usage(SimpleNamespace(usage=SimpleNamespace(input_tokens=30, output_tokens=20, cache_read_input_tokens=60, cache_creation_input_tokens=10)))
        self.assertEqual(usage.prompt_tokens, 100)
        self.assertEqual(usage.total_tokens, 120)

    def test_pricing_versions_groups_and_safe_details(self):
        old = self.call({"prompt_tokens": 100, "completion_tokens": 20, "prompt_cached_tokens": 60, "prompt_cache_creation_tokens": 10}, old=True)
        payload = {"provider_id": "demo-provider", "model": "demo", "currency": "CNY", "input_price": 10, "output_price": 20, "cache_read_price": 1, "cache_write_price": 12}
        response = self.client.post("/api/model-usage/pricing", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.call({"prompt_tokens": 100, "completion_tokens": 20, "prompt_cached_tokens": 60, "prompt_cache_creation_tokens": 10})
        result = self.client.get("/api/model-usage?period=all&details=true&provider_id=demo-provider").json()
        self.assertEqual(result["summary"]["priced_requests"], 1)
        self.assertAlmostEqual(result["summary"]["costs"]["CNY"], .00088)
        self.assertEqual(result["summary"]["cache_request_hit_rate"], 1)
        self.assertEqual(result["by_provider"][0]["requests"], 2)
        self.assertEqual(result["by_model"][0]["model"], "demo")
        self.assertIn("attempt_id", result["records"][0]["details"])
        self.assertNotIn("private-hash", str(result))
        self.assertIsNone(next(row for row in result["records"] if row["id"] == old["id"])["estimated_cost"])
        self.assertEqual(len(self.client.get("/api/model-usage/pricing").json()["rules"]), 1)
        self.assertEqual(self.client.post("/api/model-usage/pricing", json=payload | {"input_price": -1}).status_code, 422)
        self.assertEqual(self.client.post("/api/model-usage/pricing", json=payload | {"currency": "BAD"}).status_code, 422)

    def test_price_changes_preserve_history_and_currencies(self):
        from app.model_usage import PricingRule, save_pricing
        base = dict(provider_id="demo-provider", model="demo", currency="CNY", input_price=10, output_price=20, cache_read_price=10, cache_write_price=10)
        first = save_pricing(self.database, PricingRule(**base))
        before = self.call({"prompt_tokens": 100, "completion_tokens": 20})
        second = save_pricing(self.database, PricingRule(**(base | {"currency": "USD", "input_price": 20, "cache_read_price": 20, "cache_write_price": 20})))
        after = self.call({"prompt_tokens": 100, "completion_tokens": 20})
        result = self.client.get("/api/model-usage?period=all").json()
        records = {row["id"]: row for row in result["records"]}
        self.assertEqual(records[before["id"]]["pricing_id"], first["id"])
        self.assertEqual(records[after["id"]]["pricing_id"], second["id"])
        self.assertAlmostEqual(result["summary"]["costs"]["CNY"], .0014)
        self.assertAlmostEqual(result["summary"]["costs"]["USD"], .0024)
        self.assertNotIn("details", records[before["id"]])
        self.assertEqual(self.client.post("/api/model-usage/pricing", json=base | {"model": "   "}).status_code, 422)
        self.assertEqual(self.client.get("/api/model-usage?provider_id=' OR 1=1 --").json()["total"], 0)

    def test_tool_statistics_are_independent_paginated_and_private(self):
        with self.database.connect() as connection:
            for i in range(53):
                connection.execute("INSERT INTO tool_calls(id,tool,arguments_json,receipt_json,status,created_at,finished_at,external_user_id,idempotency_key) VALUES (?,?,?,?,?,?,?,?,?)", (str(i), "tasks.list", '{"secret":"private-arguments"}', '{"secret":"private-receipt"}', "succeeded", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00", "private-user", "private-key"))
        result = self.client.get("/api/model-usage/tools?period=all&limit=2&offset=50").json()
        self.assertEqual(result["total"], 53)
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["groups"][0]["succeeded"], 53)
        self.assertAlmostEqual(result["groups"][0]["average_latency_ms"], 1000, delta=1)
        self.assertNotIn("private-", str(result))
        self.assertEqual(self.client.get("/api/model-usage/tools?period=all&tool=absent").json()["total"], 0)
        self.assertEqual(self.client.get("/api/model-usage/tools?limit=501").status_code, 422)

    def test_unknown_cache_prevents_discounted_cost_estimate(self):
        payload = {"provider_id": "demo-provider", "model": "demo", "currency": "USD", "input_price": 10, "output_price": 20, "cache_read_price": 1, "cache_write_price": 12}
        self.assertEqual(self.client.post("/api/model-usage/pricing", json=payload).status_code, 200)
        self.call({"prompt_tokens": 100, "completion_tokens": 20})
        result = self.client.get("/api/model-usage").json()
        self.assertEqual(result["summary"]["priced_requests"], 0)
        self.assertIsNone(result["records"][0]["estimated_cost"])
        self.assertEqual(result["records"][0]["cost_reason"], "缓存用量未返回，无法按差异单价估算")

    def test_filters_pagination_validation_and_empty(self):
        self.call({"total_tokens": 7}, model="first")
        self.call(status="error", model="second")
        self.call({"total_tokens": 99}, old=True)
        result = self.client.get("/api/model-usage?period=7d&model=second&status=error").json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["records"][0]["http_status"], 429)
        empty = self.client.get("/api/model-usage?model=missing").json()
        self.assertEqual(empty["summary"]["requests"], 0)
        self.assertIsNone(empty["summary"]["cache_hit_rate"])
        self.assertIsNone(empty["summary"]["input_tokens"])
        self.assertIsNone(empty["summary"]["cached_tokens"])
        for query in ("period=bad", "status=bad", "limit=501", "offset=-1"):
            self.assertEqual(self.client.get(f"/api/model-usage?{query}").status_code, 422)


if __name__ == "__main__":
    unittest.main()
