from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict

from ...db import Database, utc_now
from ...diagnostics.privacy import task_scrubber


class ConfirmationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    action_kind: str | None = None
    payload: dict[str, Any] | None = None
    consumed_at: str | None = None


class ConfirmationService:
    def __init__(self, database: Database, settings):
        self.database = database
        self.scrubber = task_scrubber(settings)

    def issue(
        self,
        *,
        channel: str,
        external_user_id: str,
        session_id: str,
        inbound_message_id: str,
        action_kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
        ttl_seconds: int = 600,
    ) -> dict[str, Any]:
        cleaned = self.scrubber.clean(payload)
        encoded = json.dumps(
            cleaned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        action_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        raw_token = _token()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=max(60, min(int(ttl_seconds), 3600)))
        identifier = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO pending_actions(
                  id, channel, external_user_id, session_id, inbound_message_id,
                  action_kind, payload_json, action_hash, token_hash,
                  expires_at, state, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    identifier,
                    str(channel).strip().lower(),
                    str(external_user_id),
                    session_id,
                    inbound_message_id,
                    action_kind,
                    encoded,
                    action_hash,
                    _hash_token(raw_token),
                    expires_at.isoformat(),
                    idempotency_key,
                    utc_now(),
                ),
            )
        return {
            "id": identifier,
            "token": raw_token,
            "action_kind": action_kind,
            "action_hash": action_hash,
            "expires_at": expires_at.isoformat(),
            "payload": cleaned,
        }

    def consume(
        self,
        *,
        channel: str,
        external_user_id: str,
        session_id: str,
        token: str,
    ) -> ConfirmationResult:
        token_hash = _hash_token(token)
        now = datetime.now(timezone.utc)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM pending_actions
                WHERE token_hash = ? AND channel = ?
                  AND external_user_id = ? AND session_id = ?
                """,
                (
                    token_hash,
                    str(channel).strip().lower(),
                    str(external_user_id),
                    session_id,
                ),
            ).fetchone()
            if not row:
                return ConfirmationResult(status="invalid")
            item = dict(row)
            if item["state"] == "consumed":
                return ConfirmationResult(
                    status="already_consumed",
                    action_kind=item["action_kind"],
                    payload=json.loads(item["payload_json"]),
                    consumed_at=item["consumed_at"],
                )
            if item["state"] != "pending":
                return ConfirmationResult(status=item["state"])
            expires_at = _parse(item["expires_at"])
            if expires_at <= now:
                connection.execute(
                    "UPDATE pending_actions SET state = 'expired' WHERE id = ?",
                    (item["id"],),
                )
                return ConfirmationResult(status="expired")
            consumed_at = utc_now()
            connection.execute(
                """
                UPDATE pending_actions
                SET state = 'consumed', consumed_at = ?
                WHERE id = ? AND state = 'pending'
                """,
                (consumed_at, item["id"]),
            )
        return ConfirmationResult(
            status="confirmed",
            action_kind=item["action_kind"],
            payload=json.loads(item["payload_json"]),
            consumed_at=consumed_at,
        )

    def cancel(
        self,
        *,
        channel: str,
        external_user_id: str,
        session_id: str,
        token: str,
    ) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE pending_actions
                SET state = 'cancelled', cancelled_at = ?
                WHERE token_hash = ? AND channel = ?
                  AND external_user_id = ? AND session_id = ?
                  AND state = 'pending'
                """,
                (
                    utc_now(),
                    _hash_token(token),
                    str(channel).strip().lower(),
                    str(external_user_id),
                    session_id,
                ),
            )
        return bool(cursor.rowcount)

    def pending_count(
        self,
        *,
        channel: str,
        external_user_id: str,
        session_id: str,
    ) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) count FROM pending_actions
                WHERE channel = ? AND external_user_id = ?
                  AND session_id = ? AND state = 'pending' AND expires_at > ?
                """,
                (
                    str(channel).strip().lower(),
                    str(external_user_id),
                    session_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            ).fetchone()
        return int(row["count"])


def _token() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _hash_token(token: str) -> str:
    normalized = str(token or "").strip().upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
