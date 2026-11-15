from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Awaitable, Callable, TypeVar

from ...db import Database, utc_now
from ...diagnostics.privacy import task_scrubber


T = TypeVar("T")


class ToolCallConflict(RuntimeError):
    pass


class ToolLedger:
    def __init__(self, database: Database, settings):
        self.database = database
        self.scrubber = task_scrubber(settings)

    async def execute(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        operation: Callable[[], Awaitable[T]],
        attempt_id: str | None = None,
        channel: str | None = None,
        external_user_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> T:
        identifier = uuid.uuid4().hex
        cleaned_arguments = self.scrubber.clean(arguments)
        key = idempotency_key or hashlib.sha256(
            json.dumps(
                cleaned_arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM tool_calls
                WHERE tool = ? AND idempotency_key = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (tool, key),
            ).fetchone()
            if existing:
                if existing["status"] == "succeeded":
                    return json.loads(existing["receipt_json"] or "null")
                if existing["status"] == "running":
                    raise ToolCallConflict("该工具动作正在执行，请勿重复提交")
                raise ToolCallConflict(
                    f"该工具动作此前执行失败：{existing['error_code'] or 'unknown'}"
                )
            connection.execute(
                """
                INSERT INTO tool_calls(
                  id, attempt_id, channel, external_user_id, tool,
                  arguments_json, idempotency_key, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    identifier,
                    attempt_id,
                    channel,
                    external_user_id,
                    tool,
                    json.dumps(cleaned_arguments, ensure_ascii=False),
                    key,
                    utc_now(),
                ),
            )
        try:
            result = await operation()
        except Exception as error:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    UPDATE tool_calls
                    SET status = 'error', error_code = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (type(error).__name__[:120], utc_now(), identifier),
                )
            raise
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE tool_calls
                SET status = 'succeeded', receipt_json = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(self.scrubber.clean(result), ensure_ascii=False),
                    utc_now(),
                    identifier,
                ),
            )
        return result

    def list(
        self,
        *,
        attempt_id: str | None = None,
        channel: str | None = None,
        external_user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = []
        values: list[Any] = []
        for column, value in (
            ("attempt_id", attempt_id),
            ("channel", channel),
            ("external_user_id", external_user_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 1000)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM tool_calls
                {where}
                ORDER BY created_at DESC LIMIT ?
                """,
                values,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["arguments"] = json.loads(item.pop("arguments_json") or "{}")
            item["receipt"] = json.loads(item.pop("receipt_json") or "{}")
            result.append(item)
        return result
