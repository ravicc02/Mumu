from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from ...db import Database, utc_now
from ...diagnostics.privacy import task_scrubber
from ...version import __version__
from ..agents.eventing import OutboxService
from ..agents.leases import lease_heartbeat
from .weixin import (
    BASE_URL,
    WeixinRequestError,
    _headers,
    check_response,
    record_delivery,
)


logger = logging.getLogger(__name__)

CHANNEL = "weixin"
LEASE_KEY = "channel:weixin:poller"
STATE_KEY = "weixin_channel_state"


class WeixinUnbound(RuntimeError):
    pass


class WeixinContextRequired(RuntimeError):
    code = "context_required"


def channel_snapshot(database: Database) -> dict[str, Any]:
    binding = database.get_setting("weixin_binding", {}) or {}
    state = database.get_setting(STATE_KEY, {}) or {}
    bound_user = str(binding.get("user_id") or "")
    context = (
        get_context(database, bound_user)
        if bound_user
        else ""
    )
    configured = bool(binding.get("token") and bound_user)
    current = str(state.get("state") or "")
    if not configured and not current:
        current = "unbound"
    return {
        "state": current or ("ready" if context else "bound"),
        "configured": configured,
        "ready": bool(configured and context and current == "ready"),
        "context_ready": bool(context),
        "last_poll_at": state.get("last_poll_at"),
        "last_message_at": state.get("last_message_at"),
        "last_error": state.get("error"),
        "updated_at": state.get("updated_at"),
    }


def set_channel_state(
    database: Database,
    state: str,
    *,
    error: str | None = None,
    owner_id: str | None = None,
    message_seen: bool = False,
) -> dict[str, Any]:
    current = database.get_setting(STATE_KEY, {}) or {}
    scrubber = task_scrubber(getattr(database, "settings", database))
    now = utc_now()
    values = {
        **current,
        "state": state,
        "error": scrubber.text(str(error))[:500] if error else None,
        "updated_at": now,
    }
    if owner_id is not None:
        values["owner_id"] = owner_id
    if state in {"connecting", "ready"}:
        values["last_poll_at"] = now
    if message_seen:
        values["last_message_at"] = now
    database.set_setting(STATE_KEY, values)
    return values


def get_context(
    database: Database,
    external_user_id: str,
    channel: str = CHANNEL,
) -> str:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT context_token FROM channel_contexts
            WHERE channel = ? AND external_user_id = ?
            """,
            (str(channel).strip().lower(), str(external_user_id)),
        ).fetchone()
    if row:
        return str(row["context_token"] or "")
    binding = database.get_setting("weixin_binding", {}) or {}
    if (
        channel == CHANNEL
        and str(binding.get("user_id") or "") == str(external_user_id)
    ):
        return str(binding.get("context_token") or "")
    return ""


def ingest_updates(
    database: Database,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    messages = extract_messages(payload)
    scrubber = task_scrubber(getattr(database, "settings", database))
    now = utc_now()
    inserted: list[dict[str, Any]] = []
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        binding_row = connection.execute(
            "SELECT value FROM app_settings WHERE key = 'weixin_binding'"
        ).fetchone()
        try:
            binding = json.loads(binding_row["value"]) if binding_row else {}
        except (TypeError, json.JSONDecodeError):
            binding = {}
        bound_user = str(binding.get("user_id") or "")
        latest_bound_context = ""
        for message in messages:
            cursor = connection.execute(
                """
                INSERT INTO channel_messages(
                  id, channel, external_message_id, session_id, sender_id,
                  message_type, content_json, processed_at, created_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, NULL, ?)
                ON CONFLICT(channel, external_message_id) DO NOTHING
                """,
                (
                    uuid.uuid4().hex,
                    CHANNEL,
                    message["external_message_id"],
                    message["sender_id"],
                    message["message_type"],
                    json.dumps(
                        scrubber.clean({
                            "text": message["text"],
                            "raw_type": message["raw_type"],
                        }),
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            if message["context_token"]:
                connection.execute(
                    """
                    INSERT INTO channel_contexts(
                      channel, external_user_id, context_token, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(channel, external_user_id) DO UPDATE SET
                      context_token = excluded.context_token,
                      updated_at = excluded.updated_at
                    """,
                    (
                        CHANNEL,
                        message["sender_id"],
                        message["context_token"],
                        now,
                    ),
                )
                if message["sender_id"] == bound_user:
                    latest_bound_context = message["context_token"]
            if cursor.rowcount:
                inserted.append({
                    key: value
                    for key, value in message.items()
                    if key != "context_token"
                })

        updates_cursor = payload.get("get_updates_buf")
        if isinstance(updates_cursor, str):
            binding["get_updates_buf"] = updates_cursor
        if latest_bound_context:
            binding["context_token"] = latest_bound_context
        if binding:
            connection.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES ('weixin_binding', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value = excluded.value,
                  updated_at = excluded.updated_at
                """,
                (json.dumps(binding, ensure_ascii=False), now),
            )
    if inserted:
        set_channel_state(
            database,
            "ready",
            owner_id=(database.get_setting(STATE_KEY, {}) or {}).get("owner_id"),
            message_seen=True,
        )
    return inserted


def extract_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = (
        payload.get("msgs")
        or payload.get("messages")
        or payload.get("updates")
        or []
    )
    if isinstance(candidates, dict):
        candidates = [candidates]
    messages: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates if isinstance(candidates, list) else []):
        if not isinstance(raw, dict):
            continue
        sender = _sender_id(raw)
        if not sender:
            continue
        text = _message_text(raw).strip()
        raw_type = raw.get("message_type") or raw.get("msg_type") or ""
        message_type = "text" if text else f"unsupported:{raw_type or 'unknown'}"
        external_id = _external_message_id(raw, sender, text, index)
        context_token = str(
            raw.get("context_token")
            or raw.get("contextToken")
            or ""
        )
        messages.append({
            "external_message_id": external_id[:512],
            "sender_id": sender,
            "message_type": message_type,
            "raw_type": str(raw_type)[:80],
            "text": text[:32000],
            "context_token": context_token[:4000],
        })
    return messages


def pending_messages(database: Database, *, limit: int = 20) -> list[dict[str, Any]]:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM channel_messages
            WHERE channel = ? AND processed_at IS NULL
            ORDER BY created_at, rowid LIMIT ?
            """,
            (CHANNEL, max(1, min(int(limit), 100))),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_processed(
    database: Database,
    message_id: str,
    *,
    session_id: str | None = None,
) -> None:
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE channel_messages
            SET processed_at = ?, session_id = COALESCE(?, session_id)
            WHERE id = ?
            """,
            (utc_now(), session_id, message_id),
        )


async def send_text_message(
    database: Database,
    *,
    to_user_id: str,
    text: str,
    context_token: str | None = None,
) -> dict[str, Any]:
    binding = database.get_setting("weixin_binding", {}) or {}
    token = str(binding.get("token") or "")
    if not token or not binding.get("user_id"):
        raise WeixinUnbound("微信机器人尚未绑定")
    context = context_token or get_context(database, to_user_id)
    if not context:
        raise WeixinContextRequired(
            "尚未收到该微信用户的上下文令牌，请先让用户发送消息"
        )
    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": str(to_user_id),
            "client_id": str(uuid.uuid4()),
            "message_type": 2,
            "message_state": 2,
            "context_token": context,
            "item_list": [
                {"type": 1, "text_item": {"text": str(text)[:32000]}}
            ],
        },
        "base_info": {
            "channel_version": "2.4.10",
            "bot_agent": f"Mumu/{__version__}",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{str(binding.get('base') or BASE_URL).rstrip('/')}"
                "/ilink/bot/sendmessage",
                headers=_headers(token),
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        check_response(payload, "微信发送失败")
    except httpx.HTTPStatusError as error:
        if _is_auth_error(error):
            _clear_expired_auth(database, error)
        raise
    except WeixinRequestError as error:
        if _is_auth_error(error):
            _clear_expired_auth(database, error)
        record_delivery(database, error=error)
        raise
    record_delivery(database, result={"weixin": True})
    return {"ok": True, "to_user_id": str(to_user_id)}


async def send_outbox_message(
    database: Database,
    message: dict[str, Any],
) -> dict[str, Any]:
    if message["channel"] == "webhook":
        payload = message.get("payload") or {}
        event = payload.get("event")
        if not isinstance(event, dict):
            raise ValueError("Webhook outbox 消息缺少事件内容")
        url = str(getattr(database.settings, "webhook_url", "") or "")
        if not url:
            raise RuntimeError("Webhook 地址未配置")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=event)
            response.raise_for_status()
        return {"ok": True, "channel": "webhook"}
    if message["channel"] != CHANNEL:
        raise ValueError(f"不支持的 outbox 通道：{message['channel']}")
    payload = message.get("payload") or {}
    kind = payload.get("kind")
    if kind == "assistant_reply":
        text = str(payload.get("text") or "").strip()
    elif kind == "incident":
        text = _incident_text(payload)
    elif kind == "incident_resolved":
        text = _incident_resolved_text(payload)
    elif kind == "task_notification":
        text = str(payload.get("text") or "").strip()
    else:
        text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("outbox 消息没有可发送文本")
    return await send_text_message(
        database,
        to_user_id=message["external_user_id"],
        text=text,
    )


async def fetch_and_ingest(database: Database) -> list[dict[str, Any]]:
    binding = database.get_setting("weixin_binding", {}) or {}
    if not binding.get("token") or not binding.get("user_id"):
        set_channel_state(database, "unbound")
        raise WeixinUnbound("微信机器人尚未绑定")
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{str(binding.get('base') or BASE_URL).rstrip('/')}"
                "/ilink/bot/getupdates",
                headers=_headers(binding["token"]),
                json={
                    "get_updates_buf": binding.get("get_updates_buf", ""),
                    "base_info": {
                        "channel_version": "2.4.10",
                        "bot_agent": f"Mumu/{__version__}",
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
        check_response(payload, "微信会话刷新失败")
    except WeixinRequestError as error:
        if _is_auth_error(error):
            _clear_expired_auth(database, error)
            set_channel_state(
                database,
                "auth_required",
                error="微信授权已失效，请重新扫码绑定。",
            )
        else:
            set_channel_state(database, "degraded", error=str(error))
        record_delivery(database, error=error)
        raise
    except httpx.HTTPStatusError as error:
        if _is_auth_error(error):
            _clear_expired_auth(database, error)
            set_channel_state(
                database,
                "auth_required",
                error="微信授权已失效，请重新扫码绑定。",
            )
        else:
            set_channel_state(
                database,
                "degraded",
                error=f"微信服务返回 HTTP {error.response.status_code}。",
            )
        raise
    except httpx.HTTPError as error:
        set_channel_state(
            database,
            "degraded",
            error=f"微信网络连接失败：{type(error).__name__}",
        )
        raise
    messages = ingest_updates(database, payload)
    bound_user = str(binding.get("user_id") or "")
    connected = bool(bound_user and get_context(database, bound_user))
    delivery = database.get_setting("weixin_delivery", {}) or {}
    if delivery.get("error"):
        database.set_setting(
            "weixin_delivery",
            {
                **delivery,
                "error": None,
                "last_poll_recovered_at": utc_now(),
            },
        )
    state = set_channel_state(
        database,
        "ready" if connected else "bound",
        owner_id=(database.get_setting(STATE_KEY, {}) or {}).get("owner_id"),
    )
    return messages if messages else []


async def refresh_context(database: Database) -> dict[str, Any]:
    await fetch_and_ingest(database)
    binding = database.get_setting("weixin_binding", {}) or {}
    context = get_context(database, str(binding.get("user_id") or ""))
    return {
        "ready": bool(context),
        "message": (
            "微信会话已就绪"
            if context
            else "已绑定。请先在微信中向机器人发送一条消息，再刷新会话。"
        ),
    }


class WeixinChannel:
    def __init__(self, database: Database | Any, assistant) -> None:
        self._database_provider = (
            database if callable(database) else (lambda: database)
        )
        self.assistant = assistant
        self.owner_id = f"wechat-poller:{uuid.uuid4().hex}"
        self._poll_lock = asyncio.Lock()

    @property
    def database(self) -> Database:
        return self._database_provider()

    @property
    def outbox(self) -> OutboxService:
        return OutboxService(
            self.database,
            getattr(self.database, "settings", self.database),
        )

    async def run(self) -> None:
        while True:
            binding = self.database.get_setting("weixin_binding", {}) or {}
            if not binding.get("token") or not binding.get("user_id"):
                current = self.database.get_setting(STATE_KEY, {}) or {}
                set_channel_state(
                    self.database,
                    (
                        "auth_required"
                        if current.get("state") == "auth_required"
                        else "unbound"
                    ),
                )
                await self.dispatch_outbox()
                await asyncio.sleep(5)
                continue
            acquired, _ = self.database.acquire_resource_leases(
                owner_id=self.owner_id,
                owner_kind="wechat-poller",
                resource_keys=[LEASE_KEY],
                ttl_seconds=90,
            )
            if acquired:
                break
            logger.info(
                "Weixin poller lease is held by another Mumu instance"
            )
            await asyncio.sleep(15)

        heartbeat = asyncio.create_task(
            lease_heartbeat(
                self.database,
                self.owner_id,
                interval_seconds=15,
                ttl_seconds=90,
            )
        )
        set_channel_state(
            self.database,
            "connecting",
            owner_id=self.owner_id,
        )
        backoff = 2.0
        try:
            while True:
                binding = self.database.get_setting("weixin_binding", {}) or {}
                if not binding.get("token") or not binding.get("user_id"):
                    current = self.database.get_setting(STATE_KEY, {}) or {}
                    set_channel_state(
                        self.database,
                        (
                            "auth_required"
                            if current.get("state") == "auth_required"
                            else "unbound"
                        ),
                        owner_id=self.owner_id,
                    )
                    await self.dispatch_outbox()
                    await asyncio.sleep(5)
                    continue
                try:
                    await self.dispatch_outbox()
                    await self.process_pending()
                    async with self._poll_lock:
                        await fetch_and_ingest(self.database)
                    backoff = 2.0
                    await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    raise
                except (WeixinUnbound, WeixinRequestError) as error:
                    if _is_auth_error(error):
                        await asyncio.sleep(30)
                    else:
                        delay = _jitter(backoff)
                        backoff = min(60.0, backoff * 2)
                        await asyncio.sleep(delay)
                except Exception:
                    logger.exception("Weixin poll cycle failed")
                    set_channel_state(
                        self.database,
                        "degraded",
                        owner_id=self.owner_id,
                        error="微信通道处理失败，正在自动重试。",
                    )
                    delay = _jitter(backoff)
                    backoff = min(60.0, backoff * 2)
                    await asyncio.sleep(delay)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self.database.release_resource_leases(
                self.owner_id,
                resource_keys=[LEASE_KEY],
            )

    async def process_pending(self) -> int:
        processed = 0
        for message in pending_messages(self.database):
            content = json.loads(message.get("content_json") or "{}")
            text = str(content.get("text") or "").strip()
            result: dict[str, Any] = {}
            if text:
                result = await self.assistant.handle_message(
                    channel=CHANNEL,
                    external_user_id=message["sender_id"],
                    text=text,
                    inbound_message_id=message["external_message_id"],
                    message_type=message["message_type"],
                )
            else:
                reply = "目前只支持文字消息。"
                self.outbox.enqueue(
                    channel=CHANNEL,
                    external_user_id=message["sender_id"],
                    dedupe_key=(
                        "assistant-unsupported:"
                        f"{message['external_message_id']}"
                    ),
                    payload={"kind": "assistant_reply", "text": reply},
                )
            mark_processed(
                self.database,
                message["id"],
                session_id=result.get("session_id"),
            )
            processed += 1
        return processed

    async def dispatch_outbox(self, *, limit: int = 20) -> dict[str, int]:
        return await self.outbox.dispatch(
            lambda message: send_outbox_message(self.database, message),
            limit=limit,
        )

    async def refresh(self) -> dict[str, Any]:
        async with self._poll_lock:
            result = await refresh_context(self.database)
        await self.process_pending()
        await self.dispatch_outbox()
        return result


def _sender_id(raw: dict[str, Any]) -> str:
    value = (
        raw.get("from_user_id")
        or raw.get("sender_id")
        or raw.get("fromUserId")
        or raw.get("from")
    )
    if isinstance(value, dict):
        value = value.get("id") or value.get("user_id")
    return str(value or "").strip()[:256]


def _message_text(raw: dict[str, Any]) -> str:
    for key in ("text", "content", "message"):
        value = raw.get(key)
        if isinstance(value, str):
            return value
    texts: list[str] = []
    items = raw.get("item_list") or raw.get("items") or []
    if isinstance(items, dict):
        items = [items]
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        text_item = item.get("text_item") or item.get("textItem") or {}
        if isinstance(text_item, dict):
            text = text_item.get("text")
            if isinstance(text, str):
                texts.append(text)
        elif isinstance(item.get("text"), str):
            texts.append(item["text"])
    return "\n".join(texts)


def _external_message_id(
    raw: dict[str, Any],
    sender: str,
    text: str,
    index: int,
) -> str:
    for key in ("message_id", "msg_id", "id", "client_id"):
        value = raw.get(key)
        if value:
            return str(value)
    encoded = json.dumps(
        {"sender": sender, "text": text, "index": index},
        ensure_ascii=False,
        sort_keys=True,
    )
    return "derived:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_auth_error(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {401, 403}
    if isinstance(error, WeixinRequestError):
        if int(error.code) in {-2, -1, 401, 403}:
            return True
        return bool(re.search(
            r"auth|authorization|token|session|expired|过期|失效|未授权",
            str(error.provider_message),
            re.I,
        ))
    return False


def _clear_expired_auth(
    database: Database,
    error: Exception,
) -> None:
    binding = database.get_setting("weixin_binding", {}) or {}
    binding["token"] = ""
    binding["context_token"] = ""
    binding["get_updates_buf"] = ""
    database.set_setting("weixin_binding", binding)
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM channel_contexts WHERE channel = ?",
            (CHANNEL,),
        )
    set_channel_state(
        database,
        "auth_required",
        error=str(error),
    )


def _incident_text(payload: dict[str, Any]) -> str:
    severity = {
        "critical": "严重",
        "warning": "警告",
        "info": "提示",
    }.get(str(payload.get("severity")), "提示")
    lines = [
        f"Mumu · {severity}",
        str(payload.get("title") or "任务异常"),
        str(payload.get("details") or "").strip(),
    ]
    if payload.get("next_action"):
        lines.append(f"下一步：{payload['next_action']}")
    return "\n".join(line for line in lines if line)


def _incident_resolved_text(payload: dict[str, Any]) -> str:
    return "\n".join(line for line in (
        "Mumu · 已恢复",
        str(payload.get("title") or "任务异常"),
        str(payload.get("details") or "相关服务已恢复").strip(),
    ) if line)


def _jitter(value: float) -> float:
    return max(1.0, float(value) * random.uniform(0.75, 1.25))
