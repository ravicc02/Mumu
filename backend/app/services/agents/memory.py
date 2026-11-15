from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...db import Database, utc_now
from ...diagnostics.privacy import task_scrubber


MessageRole = Literal["system", "user", "assistant", "tool"]
MemorySource = Literal["explicit_user", "verified_fact", "confirmed_habit"]
MEMORY_SOURCES = {"explicit_user", "verified_fact", "confirmed_habit"}
SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,199}$")


class AgentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentSessionRecord(AgentRecord):
    id: str
    agent_kind: str
    channel: str
    external_user_ref: str | None = None
    status: str
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class AgentMessageRecord(AgentRecord):
    id: str
    session_id: str
    sequence: int
    role: MessageRole
    content: Any
    model_call_id: str | None = None
    kind: str
    sensitive: bool
    created_at: str


class AgentSummaryRecord(AgentRecord):
    id: str
    session_id: str
    from_sequence: int
    to_sequence: int
    summary: str
    model_call_id: str | None = None
    created_at: str


class MemoryItemRecord(AgentRecord):
    id: str
    scope: str
    kind: str
    content: str
    source_ref: str
    source_kind: MemorySource
    confidence: float
    sensitive: bool
    expires_at: str | None = None
    created_at: str
    updated_at: str


class ContextBlock(AgentRecord):
    kind: str
    priority: int
    content: str
    mandatory: bool = False


class ContextMessage(AgentRecord):
    role: MessageRole
    content: Any
    sequence: int


class ContextPackage(AgentRecord):
    session_id: str | None = None
    scope: str
    token_budget: int
    estimated_tokens: int
    blocks: list[ContextBlock]
    messages: list[ContextMessage]
    dropped: dict[str, int]


class MemoryService:
    """Persistent conversation, summary and long-term memory boundary."""

    def __init__(self, database: Database, settings):
        self.database = database
        self.scrubber = task_scrubber(settings)

    @staticmethod
    def _validate_scope(scope: str) -> str:
        normalized = str(scope or "").strip().lower()
        if not SCOPE_PATTERN.fullmatch(normalized):
            raise ValueError("记忆作用域格式无效")
        return normalized

    def _clean(self, value: Any) -> Any:
        return self.scrubber.clean(value)

    def _text(self, value: Any, *, limit: int = 32000) -> str:
        cleaned = self.scrubber.text(value)
        if len(cleaned) > limit:
            cleaned = cleaned[:limit] + "[TRUNCATED]"
        return cleaned

    @staticmethod
    def _is_sensitive(value: Any) -> bool:
        text = json.dumps(value, ensure_ascii=False, default=str)
        return any(
            marker in text
            for marker in ("[REDACTED]", "[PRIVATE]", "[PERSONAL_DATA]")
        )

    def create_session(
        self,
        *,
        agent_kind: str,
        channel: str,
        external_user_ref: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identifier = uuid.uuid4().hex
        now = utc_now()
        cleaned_title = self._text(title, limit=240) if title else None
        cleaned_metadata = self._clean(metadata or {})
        user_ref = (
            self._text(external_user_ref, limit=256)
            if external_user_ref
            else None
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_sessions(
                  id, agent_kind, channel, external_user_ref, status,
                  title, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    identifier,
                    self._text(agent_kind, limit=80),
                    self._text(channel, limit=80),
                    user_ref,
                    cleaned_title,
                    json.dumps(cleaned_metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get_session(identifier)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            raise KeyError("对话会话不存在")
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        item.pop("deleted_at", None)
        return AgentSessionRecord.model_validate(item).model_dump(mode="json")

    def find_active_session(
        self,
        *,
        agent_kind: str,
        channel: str,
        external_user_ref: str,
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM agent_sessions
                WHERE agent_kind = ? AND channel = ? AND external_user_ref = ?
                  AND status = 'active' AND deleted_at IS NULL
                ORDER BY updated_at DESC, rowid DESC LIMIT 1
                """,
                (agent_kind, channel, external_user_ref),
            ).fetchone()
        return self.get_session(row["id"]) if row else None

    def archive_active_sessions(
        self,
        *,
        agent_kind: str,
        channel: str,
        external_user_ref: str,
    ) -> int:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_sessions
                SET status = 'archived', updated_at = ?
                WHERE agent_kind = ? AND channel = ? AND external_user_ref = ?
                  AND status = 'active' AND deleted_at IS NULL
                """,
                (utc_now(), agent_kind, channel, external_user_ref),
            )
        return int(cursor.rowcount)

    def update_session_metadata(
        self,
        session_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        metadata = {**session["metadata"], **self._clean(values)}
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE agent_sessions
                SET metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(metadata, ensure_ascii=False),
                    utc_now(),
                    session_id,
                ),
            )
        return self.get_session(session_id)

    def append_message(
        self,
        *,
        session_id: str,
        role: MessageRole,
        content: Any,
        model_call_id: str | None = None,
        kind: str = "message",
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session["status"] != "active":
            raise RuntimeError("当前对话会话不能追加消息")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("未知消息角色")
        cleaned = self._clean(content)
        if cleaned in (None, "", {}, []):
            raise ValueError("消息内容不能为空")
        if model_call_id:
            with self.database.connect() as connection:
                if not connection.execute(
                    "SELECT 1 FROM model_calls WHERE id = ?",
                    (model_call_id,),
                ).fetchone():
                    raise KeyError("关联的模型调用不存在")
        identifier = uuid.uuid4().hex
        now = utc_now()
        sensitive = int(self._is_sensitive(cleaned))
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM agent_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            sequence = int(row["sequence"]) + 1
            connection.execute(
                """
                INSERT INTO agent_messages(
                  id, session_id, sequence, role, content_json,
                  model_call_id, kind, sensitive, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    session_id,
                    sequence,
                    role,
                    json.dumps(cleaned, ensure_ascii=False),
                    model_call_id,
                    self._text(kind, limit=60),
                    sensitive,
                    now,
                ),
            )
            connection.execute(
                "UPDATE agent_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        return self.get_message(identifier)

    def get_message(self, message_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        if not row:
            raise KeyError("对话消息不存在")
        item = dict(row)
        item["content"] = json.loads(item.pop("content_json"))
        item["sensitive"] = bool(item["sensitive"])
        return AgentMessageRecord.model_validate(item).model_dump(mode="json")

    def list_messages(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
        include_sensitive: bool = False,
    ) -> list[dict[str, Any]]:
        self.get_session(session_id)
        clauses = ["session_id = ?", "sequence > ?"]
        values: list[Any] = [session_id, max(0, int(after_sequence))]
        if not include_sensitive:
            clauses.append("sensitive = 0")
        values.append(max(1, min(int(limit), 1000)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM agent_messages
                WHERE {' AND '.join(clauses)}
                ORDER BY sequence LIMIT ?
                """,
                values,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["content"] = json.loads(item.pop("content_json"))
            item["sensitive"] = bool(item["sensitive"])
            result.append(item)
        return result

    def save_summary(
        self,
        *,
        session_id: str,
        from_sequence: int,
        to_sequence: int,
        summary: str,
        model_call_id: str | None = None,
    ) -> dict[str, Any]:
        self.get_session(session_id)
        start, end = int(from_sequence), int(to_sequence)
        if start < 1 or end < start:
            raise ValueError("摘要序号范围无效")
        cleaned = self._text(summary, limit=32000)
        if not cleaned.strip():
            raise ValueError("摘要内容不能为空")
        identifier = uuid.uuid4().hex
        now = utc_now()
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM agent_summaries WHERE session_id = ? AND to_sequence = ?",
                (session_id, end),
            ).fetchone()
            if existing:
                identifier = existing["id"]
                connection.execute(
                    """
                    UPDATE agent_summaries
                    SET from_sequence = ?, summary = ?, model_call_id = ?, created_at = ?
                    WHERE id = ?
                    """,
                    (start, cleaned, model_call_id, now, identifier),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO agent_summaries(
                      id, session_id, from_sequence, to_sequence,
                      summary, model_call_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        session_id,
                        start,
                        end,
                        cleaned,
                        model_call_id,
                        now,
                    ),
                )
        return self.get_summary(identifier)

    def get_summary(self, summary_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_summaries WHERE id = ?",
                (summary_id,),
            ).fetchone()
        if not row:
            raise KeyError("会话摘要不存在")
        return AgentSummaryRecord.model_validate(dict(row)).model_dump(mode="json")

    def latest_summary(self, session_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_summaries
                WHERE session_id = ?
                ORDER BY to_sequence DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return (
            AgentSummaryRecord.model_validate(dict(row)).model_dump(mode="json")
            if row
            else None
        )

    async def summarize_session(
        self,
        *,
        session_id: str,
        from_sequence: int,
        to_sequence: int,
        summarizer: Callable[[list[dict[str, Any]]], Awaitable[Any]],
        model_call_id: str | None = None,
    ) -> dict[str, Any]:
        self.get_session(session_id)
        messages = [
            message
            for message in self.list_messages(
                session_id,
                limit=1000,
                include_sensitive=True,
            )
            if int(from_sequence) <= message["sequence"] <= int(to_sequence)
        ]
        if not messages:
            raise ValueError("指定范围内没有可摘要的消息")
        result = await summarizer(messages)
        summary = (
            result.get("summary")
            if isinstance(result, dict)
            else getattr(result, "summary", None)
        )
        if summary is None:
            summary = getattr(result, "completion", result)
        return self.save_summary(
            session_id=session_id,
            from_sequence=from_sequence,
            to_sequence=to_sequence,
            summary=str(summary),
            model_call_id=model_call_id,
        )

    def remember(
        self,
        *,
        scope: str,
        kind: str,
        content: str,
        source_ref: str,
        source_kind: MemorySource,
        confidence: float = 1,
        sensitive: bool = False,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        normalized_scope = self._validate_scope(scope)
        if source_kind not in MEMORY_SOURCES:
            raise ValueError("长期记忆只接受用户明确表达、已核验事实或已确认习惯")
        cleaned_content = self._text(content, limit=20000).strip()
        if not cleaned_content:
            raise ValueError("长期记忆内容不能为空")
        cleaned_source = self._text(source_ref, limit=512).strip()
        if not cleaned_source:
            raise ValueError("长期记忆必须保存来源引用")
        if expires_at:
            _parse_timestamp(expires_at)
        identifier = uuid.uuid4().hex
        now = utc_now()
        item_sensitive = bool(sensitive or self._is_sensitive(cleaned_content))
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_items(
                  id, scope, kind, content, source_ref, source_kind,
                  confidence, sensitive, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    normalized_scope,
                    self._text(kind, limit=80),
                    cleaned_content,
                    cleaned_source,
                    source_kind,
                    min(1.0, max(0.0, float(confidence))),
                    int(item_sensitive),
                    expires_at,
                    now,
                    now,
                ),
            )
        return self.get_memory(identifier)

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id = ? AND deleted_at IS NULL",
                (memory_id,),
            ).fetchone()
        if not row:
            raise KeyError("长期记忆不存在")
        item = dict(row)
        item["sensitive"] = bool(item["sensitive"])
        item.pop("deleted_at", None)
        return MemoryItemRecord.model_validate(item).model_dump(mode="json")

    def search_memories(
        self,
        *,
        scope: str,
        query: str = "",
        limit: int = 12,
        include_sensitive: bool = False,
    ) -> list[dict[str, Any]]:
        normalized_scope = self._validate_scope(scope)
        now = utc_now()
        clauses = [
            "deleted_at IS NULL",
            "(expires_at IS NULL OR expires_at > ?)",
        ]
        values: list[Any] = [now]
        if normalized_scope == "global":
            clauses.append("scope = 'global'")
        else:
            clauses.append("scope IN (?, 'global')")
            values.append(normalized_scope)
        if not include_sensitive:
            clauses.append("sensitive = 0")
        cleaned_query = self._text(query, limit=500).strip()
        if cleaned_query:
            tokens = [
                item
                for item in re.split(r"\s+", cleaned_query)
                if len(item) >= 2
            ][:8]
            if not tokens:
                tokens = [cleaned_query]
            clauses.append(
                "(" + " OR ".join("content LIKE ?" for _ in tokens) + ")"
            )
            values.extend(f"%{token}%" for token in tokens)
        values.append(max(1, min(int(limit), 100)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM memory_items
                WHERE {' AND '.join(clauses)}
                ORDER BY
                  CASE source_kind
                    WHEN 'explicit_user' THEN 0
                    WHEN 'verified_fact' THEN 1
                    ELSE 2
                  END,
                  confidence DESC,
                  updated_at DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["sensitive"] = bool(item["sensitive"])
            item.pop("deleted_at", None)
            result.append(MemoryItemRecord.model_validate(item).model_dump(mode="json"))
        return result

    def forget_memory(
        self,
        *,
        memory_id: str | None = None,
        scope: str | None = None,
        query: str | None = None,
        include_sensitive: bool = True,
        reason: str = "user_request",
    ) -> int:
        if not memory_id and not scope and query is None:
            raise ValueError("遗忘操作必须指定记忆、作用域或查询")
        clauses = ["deleted_at IS NULL"]
        values: list[Any] = []
        if memory_id:
            clauses.append("id = ?")
            values.append(memory_id)
        if scope:
            clauses.append("scope = ?")
            values.append(self._validate_scope(scope))
        if query is not None:
            cleaned = self._text(query, limit=500)
            clauses.append("content LIKE ?")
            values.append(f"%{cleaned}%")
        if not include_sensitive:
            clauses.append("sensitive = 0")
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT id, scope FROM memory_items WHERE {' AND '.join(clauses)}",
                values,
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE memory_items SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, row["id"]),
                )
                self._tombstone(
                    connection,
                    target_kind="memory_item",
                    target_id=row["id"],
                    scope=row["scope"],
                    reason=reason,
                )
        return len(rows)

    def delete_session(
        self,
        session_id: str,
        *,
        reason: str = "user_delete",
    ) -> None:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM agent_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                raise KeyError("对话会话不存在")
            connection.execute(
                """
                UPDATE agent_sessions
                SET status = 'deleted', deleted_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, session_id),
            )
            connection.execute(
                "DELETE FROM agent_messages WHERE session_id = ?",
                (session_id,),
            )
            connection.execute(
                "DELETE FROM agent_summaries WHERE session_id = ?",
                (session_id,),
            )
            self._tombstone(
                connection,
                target_kind="agent_session",
                target_id=session_id,
                scope=None,
                reason=reason,
            )

    def prune(
        self,
        *,
        conversation_retention_days: int = 30,
        memory_retention_days: int = 365,
        now: datetime | None = None,
    ) -> dict[str, int]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        conversation_cutoff = (
            current - timedelta(days=max(1, int(conversation_retention_days)))
        ).isoformat()
        memory_cutoff = (
            current - timedelta(days=max(1, int(memory_retention_days)))
        ).isoformat()
        deleted_messages = 0
        forgotten_memories = 0
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            message_rows = connection.execute(
                """
                SELECT session_id, COUNT(*) AS count
                FROM agent_messages
                WHERE created_at < ?
                GROUP BY session_id
                """,
                (conversation_cutoff,),
            ).fetchall()
            for row in message_rows:
                deleted_messages += int(row["count"])
                self._tombstone(
                    connection,
                    target_kind="agent_messages",
                    target_id=row["session_id"],
                    scope=None,
                    reason="conversation_retention",
                )
            connection.execute(
                "DELETE FROM agent_messages WHERE created_at < ?",
                (conversation_cutoff,),
            )
            memory_rows = connection.execute(
                """
                SELECT id, scope FROM memory_items
                WHERE deleted_at IS NOT NULL
                   OR (expires_at IS NOT NULL AND expires_at <= ?)
                   OR updated_at < ?
                """,
                (current.isoformat(), memory_cutoff),
            ).fetchall()
            for row in memory_rows:
                forgotten_memories += 1
                self._tombstone(
                    connection,
                    target_kind="memory_item",
                    target_id=row["id"],
                    scope=row["scope"],
                    reason="memory_retention",
                )
                connection.execute(
                    "DELETE FROM memory_items WHERE id = ?",
                    (row["id"],),
                )
        return {
            "messages": deleted_messages,
            "memories": forgotten_memories,
        }

    def recover_session(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        summary = self.latest_summary(session_id)
        messages = self.list_messages(
            session_id,
            after_sequence=summary["to_sequence"] if summary else 0,
            limit=50,
            include_sensitive=False,
        )
        return {
            "session": session,
            "summary": summary,
            "messages": messages,
        }

    def assemble(
        self,
        *,
        scope: str,
        query: str = "",
        token_budget: int = 6000,
        session_id: str | None = None,
        system_context: Any = None,
        platform_snapshot: Any = None,
        task_attempt_ids: list[str] | None = None,
        include_sensitive: bool = False,
    ) -> ContextPackage:
        normalized_scope = self._validate_scope(scope)
        budget = max(512, min(int(token_budget), 100_000))
        blocks: list[ContextBlock] = []
        dropped = {"messages": 0, "memories": 0, "blocks": 0}
        remaining = budget

        def add_block(
            kind: str,
            content: Any,
            *,
            priority: int,
            mandatory: bool = False,
            max_tokens: int | None = None,
        ) -> None:
            nonlocal remaining
            cleaned = self._clean(content)
            text = (
                cleaned
                if isinstance(cleaned, str)
                else json.dumps(
                    cleaned,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            estimated = _tokens(text)
            if max_tokens and estimated > max_tokens:
                text = text[: max(32, int(max_tokens) * 4)] + "[TRUNCATED]"
                estimated = _tokens(text)
            if not mandatory and estimated > remaining:
                dropped["blocks"] += 1
                return
            if mandatory and estimated > remaining:
                text = text[: max(32, remaining * 4)] + "[TRUNCATED]"
                estimated = _tokens(text)
            remaining = max(0, remaining - estimated)
            blocks.append(
                ContextBlock(
                    kind=kind,
                    priority=priority,
                    content=text,
                    mandatory=mandatory,
                )
            )

        if system_context is not None:
            add_block(
                "system_identity",
                system_context,
                priority=100,
                mandatory=True,
                max_tokens=max(32, budget // 4),
            )

        checkpoint_blocks = self._task_context_blocks(task_attempt_ids or [])
        for checkpoint in checkpoint_blocks:
            compact = (
                f"任务 {checkpoint['attempt_id']} "
                f"[{checkpoint.get('attempt_kind')}] "
                f"状态={checkpoint.get('status')} 阶段={checkpoint.get('phase')}\n"
                f"阻塞={json.dumps(checkpoint.get('blockers') or [], ensure_ascii=False)}\n"
                f"目标={json.dumps(checkpoint.get('goal') or [], ensure_ascii=False)}\n"
                f"已完成={json.dumps(checkpoint.get('completed') or [], ensure_ascii=False)}\n"
                f"下一步={checkpoint.get('next_action_id') or '无'}\n"
                f"恢复规则={checkpoint.get('resume_policy')}"
            )
            add_block(
                "task_checkpoint",
                compact,
                priority=95,
                mandatory=True,
                max_tokens=max(48, budget // 5),
            )

        summary = self.latest_summary(session_id) if session_id else None
        if summary:
            add_block(
                "session_summary",
                summary["summary"],
                priority=80,
            )

        if platform_snapshot is not None:
            add_block(
                "platform_snapshot",
                platform_snapshot,
                priority=70,
            )

        memories = self.search_memories(
            scope=normalized_scope,
            query=query,
            limit=12,
            include_sensitive=include_sensitive,
        )
        for item in memories:
            estimated = _tokens(item["content"])
            if estimated > remaining:
                dropped["memories"] += 1
                continue
            add_block(
                "long_term_memory",
                {
                    "kind": item["kind"],
                    "content": item["content"],
                    "source_kind": item["source_kind"],
                    "confidence": item["confidence"],
                },
                priority=50,
            )

        context_messages: list[ContextMessage] = []
        if session_id:
            after_sequence = summary["to_sequence"] if summary else 0
            with self.database.connect() as connection:
                clauses = ["session_id = ?", "sequence > ?"]
                values: list[Any] = [session_id, after_sequence]
                if not include_sensitive:
                    clauses.append("sensitive = 0")
                rows = connection.execute(
                    f"""
                    SELECT role, content_json, sequence
                    FROM agent_messages
                    WHERE {' AND '.join(clauses)}
                    ORDER BY sequence DESC LIMIT 80
                    """,
                    values,
                ).fetchall()
            for row in rows:
                content = json.loads(row["content_json"])
                estimated = _tokens(content)
                if estimated > remaining:
                    dropped["messages"] += 1
                    continue
                remaining -= estimated
                context_messages.append(
                    ContextMessage(
                        role=row["role"],
                        content=content,
                        sequence=int(row["sequence"]),
                    )
                )
            context_messages.reverse()

        return ContextPackage(
            session_id=session_id,
            scope=normalized_scope,
            token_budget=budget,
            estimated_tokens=budget - remaining,
            blocks=blocks,
            messages=context_messages,
            dropped=dropped,
        )

    def _task_context_blocks(
        self,
        attempt_ids: list[str],
    ) -> list[dict[str, Any]]:
        result = []
        for attempt_id in list(dict.fromkeys(attempt_ids))[:8]:
            checkpoint = self.database.latest_task_checkpoint(attempt_id)
            if not checkpoint:
                continue
            status = None
            phase = checkpoint["phase"]
            with self.database.connect() as connection:
                job = connection.execute(
                    "SELECT status, phase, error FROM jobs WHERE id = ?",
                    (attempt_id,),
                ).fetchone()
                if job:
                    status, phase = job["status"], job["phase"] or phase
                else:
                    has_learning = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_runs'"
                    ).fetchone()
                    if has_learning:
                        run = connection.execute(
                            "SELECT status, phase, error FROM learning_runs WHERE id = ?",
                            (attempt_id,),
                        ).fetchone()
                        if run:
                            status, phase = run["status"], run["phase"] or phase
            result.append({
                "attempt_id": attempt_id,
                "attempt_kind": checkpoint["attempt_kind"],
                "status": status,
                "phase": phase,
                "goal": checkpoint["goal"],
                "completed": checkpoint["completed"],
                "blockers": checkpoint["blockers"],
                "next_action_id": checkpoint["next_action_id"],
                "resume_policy": (
                    "先读取当前平台事实和最新证据；结果未知或已有副作用时"
                    "不得自动重放，必须重新核验。"
                ),
            })
        return result

    @staticmethod
    def _tombstone(
        connection,
        *,
        target_kind: str,
        target_id: str,
        scope: str | None,
        reason: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_tombstones(
              id, target_kind, target_id, scope, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                target_kind,
                target_id,
                scope,
                reason,
                utc_now(),
            ),
        )


def _tokens(value: Any) -> int:
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=str)
    )
    return max(1, math.ceil(len(text) / 4))


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as error:
        raise ValueError("记忆过期时间格式无效") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
