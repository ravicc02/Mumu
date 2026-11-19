"""本地模型和工具用量，仅暴露安全元数据；计价规则按生效时间保留版本。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class PricingRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    provider_id: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    currency: Literal["CNY", "USD"] = "CNY"
    input_price: float = Field(ge=0, le=1000000)
    output_price: float = Field(ge=0, le=1000000)
    cache_read_price: float = Field(ge=0, le=1000000)
    cache_write_price: float = Field(ge=0, le=1000000)


def pricing_rules(database):
    return database.get_setting("model_usage_pricing", [])


def save_pricing(database, payload):
    rule = payload.model_dump() | {"id": uuid4().hex, "effective_at": datetime.now(timezone.utc).isoformat()}
    # 在同一事务内追加，避免并发配置覆盖；历史单价不可覆盖或回填到旧请求。
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM app_settings WHERE key = 'model_usage_pricing'").fetchone()
        rules = json.loads(row[0]) if row else []
        rules.append(rule)
        connection.execute("INSERT INTO app_settings(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", ("model_usage_pricing", json.dumps(rules), rule["effective_at"]))
    return rule


def normalize_usage(raw: Any) -> dict[str, int | None]:
    usage = raw if isinstance(raw, dict) else {}

    def number(value):
        if isinstance(value, bool):
            return None
        try:
            return max(0, int(value)) if value is not None else None
        except (ValueError, TypeError, OverflowError):
            return None

    prompt = number(usage.get("prompt_tokens", usage.get("input_tokens")))
    output = number(usage.get("completion_tokens", usage.get("output_tokens")))
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached = number(usage.get("prompt_cached_tokens", usage.get("cache_read_input_tokens", details.get("cached_tokens") if isinstance(details, dict) else None)))
    creation = number(usage.get("prompt_cache_creation_tokens", usage.get("cache_creation_input_tokens")))
    if prompt is not None and "input_tokens" in usage and ("cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage):
        prompt += (cached or 0) + (creation or 0)
    total = number(usage.get("total_tokens"))
    if total is None and prompt is not None and output is not None:
        total = prompt + output
    return {"input_tokens": prompt, "output_tokens": output, "total_tokens": total, "cached_tokens": cached, "cache_creation_tokens": creation}


def estimate_cost(record, rules):
    applicable = [r for r in rules if r["provider_id"] == record["provider_id"] and r["model"] == record["model"] and datetime.fromisoformat(r["effective_at"]) <= datetime.fromisoformat(record["created_at"])]
    if not applicable:
        return {"estimated_cost": None, "currency": None, "cost_reason": "未配置该请求发生时的单价"}
    rule = max(applicable, key=lambda r: r["effective_at"])
    result = {"estimated_cost": None, "currency": rule["currency"], "pricing_id": rule["id"], "cost_reason": "输入或输出用量未返回"}
    if record["input_tokens"] is None or record["output_tokens"] is None:
        return result
    inp, out, read, write = (Decimal(str(rule[k])) for k in ("input_price", "output_price", "cache_read_price", "cache_write_price"))
    cached, creation = record["cached_tokens"], record["cache_creation_tokens"]
    if (cached is None and inp != read) or (creation is None and inp != write):
        return result | {"cost_reason": "缓存用量未返回，无法按差异单价估算"}
    normal = record["input_tokens"] - (cached or 0) - (creation or 0)
    if normal < 0:
        return result | {"cost_reason": "缓存与输入用量不一致"}
    cost = (normal * inp + record["output_tokens"] * out + (cached or 0) * read + (creation or 0) * write) / Decimal(1000000)
    return result | {"estimated_cost": float(cost), "cost_reason": None}


def aggregate(records):
    keys = ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "cache_creation_tokens")
    result = {"requests": len(records), "succeeded": sum(r["status"] == "succeeded" for r in records), "errors": sum(r["status"] == "error" for r in records), "usage_requests": sum(r["total_tokens"] is not None for r in records)}
    for key in keys:
        known = [r[key] for r in records if r[key] is not None]
        result[key] = sum(known) if known else None
    cache = [r for r in records if r["cached_tokens"] is not None and r["input_tokens"] is not None]
    denominator = sum(r["input_tokens"] for r in cache)
    result["cache_hit_rate"] = sum(r["cached_tokens"] for r in cache) / denominator if denominator else None
    result["cache_requests"] = len(cache)
    result["cache_request_hit_rate"] = sum(r["cached_tokens"] > 0 for r in cache) / len(cache) if cache else None
    latencies = [r["latency_ms"] for r in records if r["latency_ms"] is not None and r["status"] != "running"]
    result["average_latency_ms"] = round(sum(latencies) / len(latencies)) if latencies else None
    priced = [r for r in records if r["estimated_cost"] is not None]
    result["priced_requests"] = len(priced)
    result["costs"] = {currency: float(sum(Decimal(str(r["estimated_cost"])) for r in priced if r["currency"] == currency)) for currency in sorted({r["currency"] for r in priced})}
    return result


def tool_usage(database, *, period="24h", tool="", status="all", limit=50, offset=0):
    clauses, values = [], []
    if period != "all":
        clauses.append("julianday(created_at) >= julianday(?)")
        values.append((datetime.now(timezone.utc) - timedelta(hours={"24h": 24, "7d": 168, "30d": 720}[period])).isoformat())
    for key, value in (("tool", tool), ("status", status if status != "all" else "")):
        if value:
            clauses.append(f"{key} = ?")
            values.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database.connect() as connection:
        tools = [r[0] for r in connection.execute("SELECT DISTINCT tool FROM tool_calls ORDER BY tool")]
        groups = [dict(r) for r in connection.execute("SELECT tool, COUNT(*) AS requests, SUM(status='succeeded') AS succeeded, SUM(status='error') AS errors, SUM(status='running') AS running, AVG(CASE WHEN finished_at IS NOT NULL AND julianday(finished_at)>=julianday(created_at) THEN (julianday(finished_at)-julianday(created_at))*86400000 END) AS average_latency_ms FROM tool_calls" + where + " GROUP BY tool ORDER BY requests DESC,tool", values)]
        records = [dict(r) for r in connection.execute("SELECT id,attempt_id,channel,tool,status,error_code,created_at,finished_at, CASE WHEN julianday(finished_at)>=julianday(created_at) THEN (julianday(finished_at)-julianday(created_at))*86400000 END AS latency_ms FROM tool_calls" + where + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", values + [limit, offset])]
    return {"groups": groups, "records": records, "tools": tools, "total": sum(g["requests"] for g in groups), "limit": limit, "offset": offset}


def model_usage(database, *, period="24h", model="", provider_id="", status="all", limit=50, offset=0, providers=(), details=False):
    clauses, values = [], []
    if period != "all":
        since = datetime.now(timezone.utc) - timedelta(hours={"24h": 24, "7d": 168, "30d": 720}[period])
        clauses.append("julianday(created_at) >= julianday(?)")
        values.append(since.isoformat())
    for key, value in (("model", model), ("provider_id", provider_id), ("status", status if status != "all" else "")):
        if value:
            clauses.append(f"{key} = ?")
            values.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    names = {item["id"]: item["name"] for item in providers}
    rules = pricing_rules(database)
    records = []
    with database.connect() as connection:
        models = [row[0] for row in connection.execute("SELECT DISTINCT model FROM model_calls ORDER BY model")]
        provider_ids = sorted(set(names) | {row[0] for row in connection.execute("SELECT DISTINCT provider_id FROM model_calls")})
        rows = connection.execute("SELECT id, created_at, finished_at, attempt_id, role, candidate_index, provider_id, model, status, latency_ms, http_status, usage_json FROM model_calls" + where + " ORDER BY created_at DESC, rowid DESC", values)
        for row in rows:
            try:
                usage = normalize_usage(json.loads(row["usage_json"] or "null"))
            except (TypeError, ValueError):
                usage = normalize_usage(None)
            record = {key: row[key] for key in ("id", "created_at", "provider_id", "model", "status", "latency_ms", "http_status")} | usage | {"provider_name": names.get(row["provider_id"], row["provider_id"])}
            record.update(estimate_cost(record, rules))
            if details:
                record["details"] = {key: row[key] for key in ("attempt_id", "role", "candidate_index", "finished_at")}
            records.append(record)
    by_provider, by_model = {}, {}
    for record in records:
        by_provider.setdefault(record["provider_id"], []).append(record)
        by_model.setdefault((record["provider_id"], record["model"]), []).append(record)
    return {"summary": aggregate(records), "records": records[offset:offset + limit], "total": len(records), "models": models, "providers": [{"id": pid, "name": names.get(pid, pid)} for pid in provider_ids], "by_provider": [{"provider_id": pid, "provider_name": names.get(pid, pid)} | aggregate(items) for pid, items in by_provider.items()], "by_model": [{"provider_id": pid, "provider_name": names.get(pid, pid), "model": name} | aggregate(items) for (pid, name), items in by_model.items()], "limit": limit, "offset": offset}
