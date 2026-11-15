from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from ...db import Database, utc_now
from ...diagnostics.privacy import task_scrubber


class OutboxRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    channel: str
    external_user_id: str
    dedupe_key: str
    payload: dict[str, Any]
    state: str
    attempts: int
    next_attempt_at: str
    last_error: str | None = None
    created_at: str
    sent_at: str | None = None


class OutboxService:
    def __init__(self, database: Database, settings):
        self.database = database
        self.scrubber = task_scrubber(settings)

    def enqueue(
        self,
        *,
        channel: str,
        external_user_id: str,
        dedupe_key: str,
        payload: dict[str, Any],
        delay_seconds: int = 0,
    ) -> dict[str, Any]:
        cleaned = self.scrubber.clean(payload)
        now = datetime.now(timezone.utc)
        next_attempt = (
            now + timedelta(seconds=max(0, int(delay_seconds)))
        ).isoformat()
        identifier = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO outbox_messages(
                  id, channel, external_user_id, dedupe_key, payload_json,
                  state, attempts, next_attempt_at, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                ON CONFLICT(channel, dedupe_key) DO NOTHING
                """,
                (
                    identifier,
                    str(channel).strip().lower(),
                    str(external_user_id),
                    str(dedupe_key),
                    json.dumps(cleaned, ensure_ascii=False),
                    next_attempt,
                    utc_now(),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM outbox_messages
                WHERE channel = ? AND dedupe_key = ?
                """,
                (str(channel).strip().lower(), str(dedupe_key)),
            ).fetchone()
        return self._record(row)

    def claim_ready(self, *, limit: int = 10) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        claimed = []
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM outbox_messages
                WHERE state = 'pending' AND next_attempt_at <= ?
                ORDER BY next_attempt_at, created_at LIMIT ?
                """,
                (now, max(1, min(int(limit), 100))),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE outbox_messages
                    SET state = 'sending', next_attempt_at = ?
                    WHERE id = ? AND state = 'pending'
                    """,
                    (
                        (
                            datetime.now(timezone.utc) + timedelta(seconds=60)
                        ).isoformat(),
                        row["id"],
                    ),
                )
                claimed.append(dict(row))
        return [self._record({**row, "state": "sending"}) for row in claimed]

    def mark_sent(self, message_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE outbox_messages
                SET state = 'sent', attempts = attempts + 1,
                    sent_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (utc_now(), message_id),
            )

    def mark_failed(
        self,
        message_id: str,
        *,
        error_code: str,
        retry_after_seconds: float = 5,
    ) -> None:
        next_attempt = (
            datetime.now(timezone.utc)
            + timedelta(seconds=max(1, float(retry_after_seconds)))
        ).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE outbox_messages
                SET state = 'pending', attempts = attempts + 1,
                    next_attempt_at = ?, last_error = ?
                WHERE id = ?
                """,
                (next_attempt, str(error_code)[:120], message_id),
            )

    def recover_stale(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE outbox_messages
                SET state = 'pending'
                WHERE state = 'sending' AND next_attempt_at <= ?
                """,
                (now,),
            )
        return int(cursor.rowcount)

    def list_messages(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if state:
                rows = connection.execute(
                    """
                    SELECT * FROM outbox_messages WHERE state = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (state, max(1, min(int(limit), 1000))),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM outbox_messages ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(int(limit), 1000)),),
                ).fetchall()
        return [self._record(row) for row in rows]

    async def dispatch(
        self,
        sender: Callable[[dict[str, Any]], Any],
        *,
        limit: int = 10,
    ) -> dict[str, int]:
        self.recover_stale()
        sent = failed = 0
        for message in self.claim_ready(limit=limit):
            try:
                await sender(message)
            except Exception as error:
                failed += 1
                code = getattr(error, "code", type(error).__name__)
                retry_after = min(
                    300,
                    2 ** min(8, max(1, int(message["attempts"]) + 1)),
                )
                self.mark_failed(
                    message["id"],
                    error_code=str(code),
                    retry_after_seconds=retry_after,
                )
            else:
                sent += 1
                self.mark_sent(message["id"])
        return {"sent": sent, "failed": failed}

    def _record(self, row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return OutboxRecord.model_validate(item).model_dump(mode="json")


class IncidentCenter:
    def __init__(self, database: Database, outbox: OutboxService, settings):
        self.database = database
        self.outbox = outbox
        self.scrubber = task_scrubber(settings)

    def open(
        self,
        *,
        dedupe_key: str,
        severity: str,
        title: str,
        details: str,
        source_ref: str | None = None,
        next_action: str | None = None,
        recipients: list[str] | None = None,
        channel: str = "weixin",
    ) -> dict[str, Any]:
        if severity not in {"info", "warning", "critical"}:
            raise ValueError("未知事故等级")
        now = utc_now()
        cleaned_title = self.scrubber.text(title)[:500]
        cleaned_details = self.scrubber.text(details)[:4000]
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM incidents WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            notify_open = row is None or row["status"] == "resolved"
            escalate = bool(
                row
                and row["status"] == "open"
                and _severity_rank(severity) > _severity_rank(row["severity"])
            )
            if row:
                identifier = row["id"]
                connection.execute(
                    """
                    UPDATE incidents
                    SET severity = ?, status = 'open', title = ?, details = ?,
                        source_ref = ?, next_action = ?, last_seen_at = ?,
                        resolved_at = NULL
                    WHERE id = ?
                    """,
                    (
                        severity,
                        cleaned_title,
                        cleaned_details,
                        source_ref,
                        next_action,
                        now,
                        identifier,
                    ),
                )
            else:
                identifier = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO incidents(
                      id, dedupe_key, severity, status, title, details,
                      source_ref, next_action, opened_at, last_seen_at
                    ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        dedupe_key,
                        severity,
                        cleaned_title,
                        cleaned_details,
                        source_ref,
                        next_action,
                        now,
                        now,
                    ),
                )
        for recipient in dict.fromkeys(recipients or []):
            if notify_open:
                dedupe = f"incident:{identifier}:opened:{now}"
            elif escalate:
                dedupe = f"incident:{identifier}:escalated:{severity}:{now}"
            else:
                continue
            self.outbox.enqueue(
                channel=channel,
                external_user_id=recipient,
                dedupe_key=dedupe,
                payload={
                    "kind": "incident",
                    "severity": severity,
                    "title": cleaned_title,
                    "details": cleaned_details,
                    "next_action": next_action,
                },
            )
        return self.get(identifier)

    def resolve(
        self,
        dedupe_key: str,
        *,
        details: str = "",
        recipients: list[str] | None = None,
        channel: str = "weixin",
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM incidents WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            if not row:
                raise KeyError("事故不存在")
            if row["status"] == "resolved":
                return self._record(row)
            connection.execute(
                """
                UPDATE incidents
                SET status = 'resolved', details = ?, last_seen_at = ?,
                    resolved_at = ?
                WHERE id = ?
                """,
                (
                    self.scrubber.text(details or row["details"])[:4000],
                    now,
                    now,
                    row["id"],
                ),
            )
            identifier = row["id"]
        for recipient in dict.fromkeys(recipients or []):
            self.outbox.enqueue(
                channel=channel,
                external_user_id=recipient,
                dedupe_key=f"incident:{identifier}:resolved:{now}",
                payload={
                    "kind": "incident_resolved",
                    "severity": row["severity"],
                    "title": row["title"],
                    "details": details or "事故已恢复",
                },
            )
        return self.get(identifier)

    def get(self, incident_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE id = ?",
                (incident_id,),
            ).fetchone()
        if not row:
            raise KeyError("事故不存在")
        return self._record(row)

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if status:
                rows = connection.execute(
                    """
                    SELECT * FROM incidents WHERE status = ?
                    ORDER BY
                      CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                      last_seen_at DESC LIMIT ?
                    """,
                    (status, max(1, min(int(limit), 1000))),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM incidents
                    ORDER BY last_seen_at DESC LIMIT ?
                    """,
                    (max(1, min(int(limit), 1000)),),
                ).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _record(row) -> dict[str, Any]:
        return dict(row)


def _severity_rank(value: str) -> int:
    return {"info": 1, "warning": 2, "critical": 3}.get(value, 0)
