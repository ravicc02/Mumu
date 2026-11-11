from __future__ import annotations

import random
import uuid
from typing import Any
from urllib.parse import urljoin

import httpx

from ..db import Database


BASE_URL = "https://ilinkai.weixin.qq.com"
CLIENT_VERSION = str((2 << 16) | (4 << 8) | 10)
PENDING_BINDINGS: dict[str, dict[str, Any]] = {}


def _headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": CLIENT_VERSION,
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": __import__("base64").b64encode(
            str(random.getrandbits(32)).encode("ascii")
        ).decode(),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def begin_binding(database: Database) -> dict[str, Any]:
    existing = database.get_setting("weixin_binding", {}) or {}
    body = {
        "local_token_list": (
            [existing.get("token")] if existing.get("token") else []
        )
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{BASE_URL}/ilink/bot/get_bot_qrcode?bot_type=3",
            headers=_headers(),
            json=body,
        )
        response.raise_for_status()
        payload = response.json()
    code = payload.get("qrcode")
    qr_text = payload.get("qrcode_img_content")
    if not code or not qr_text:
        raise RuntimeError("微信未返回二维码")
    PENDING_BINDINGS[code] = {
        "base": BASE_URL,
        "qr_text": qr_text,
        "verify_code": "",
    }
    return {"code": code, "qr_text": qr_text}


async def check_binding(
    database: Database,
    code: str,
    verify_code: str = "",
) -> dict[str, Any]:
    pending = PENDING_BINDINGS.get(code)
    if not pending:
        raise RuntimeError("二维码已过期，请重新获取")
    pending["verify_code"] = verify_code or pending["verify_code"]
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.get(
            f"{pending['base']}/ilink/bot/get_qrcode_status",
            params={
                "qrcode": code,
                **({"verify_code": pending["verify_code"]} if pending["verify_code"] else {}),
            },
            headers=_headers(),
        )
        response.raise_for_status()
        payload = response.json()
    status = payload.get("status", "wait")
    if status == "scaned_but_redirect" and payload.get("redirect_host"):
        pending["base"] = f"https://{payload['redirect_host']}"
    if status != "confirmed":
        return {"status": status}
    required = ("bot_token", "ilink_bot_id", "ilink_user_id")
    if not all(payload.get(key) for key in required):
        raise RuntimeError("微信确认结果缺少机器人凭据")
    binding = {
        "token": payload["bot_token"],
        "bot_id": payload["ilink_bot_id"],
        "user_id": payload["ilink_user_id"],
        "base": payload.get("baseurl") or pending["base"],
        "context_token": "",
    }
    database.set_setting("weixin_binding", binding)
    PENDING_BINDINGS.pop(code, None)
    return {"status": "confirmed", "binding": binding}


async def refresh_context(database: Database) -> dict[str, Any]:
    binding = database.get_setting("weixin_binding", {}) or {}
    if not binding.get("token") or not binding.get("user_id"):
        raise RuntimeError("微信机器人尚未绑定")
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.post(
            f"{binding.get('base', BASE_URL)}/ilink/bot/getupdates",
            headers=_headers(binding["token"]),
            json={
                "get_updates_buf": "",
                "base_info": {
                    "channel_version": "2.4.10",
                    "bot_agent": "AIAssignmentStudio/0.4.0",
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
    context_token = _find_context_token(payload)
    if context_token:
        binding["context_token"] = context_token
        database.set_setting("weixin_binding", binding)
    return {
        "ready": bool(binding.get("context_token")),
        "message": (
            "微信会话已就绪"
            if context_token
            else "已绑定。请先在微信中向机器人发送一条消息，再刷新会话。"
        ),
    }


def _find_context_token(value: Any) -> str:
    if isinstance(value, dict):
        token = value.get("context_token")
        if isinstance(token, str) and token:
            return token
        for child in value.values():
            found = _find_context_token(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_context_token(child)
            if found:
                return found
    return ""
