from __future__ import annotations

import base64
import random
import uuid
from typing import Any

import httpx


async def send_completion_notification(
    settings: Any,
    payload: dict[str, Any],
) -> dict[str, bool]:
    result = {"webhook": False, "weixin": False}
    async with httpx.AsyncClient(timeout=20) as client:
        if settings.webhook_url:
            response = await client.post(settings.webhook_url, json=payload)
            response.raise_for_status()
            result["webhook"] = True
        if (
            settings.weixin_bot_token
            and settings.weixin_user_id
            and settings.weixin_context_token
        ):
            text = _weixin_text(payload)
            headers = {
                "iLink-App-Id": "bot",
                "iLink-App-ClientVersion": str((2 << 16) | (4 << 8) | 10),
                "Content-Type": "application/json",
                "AuthorizationType": "ilink_bot_token",
                "Authorization": f"Bearer {settings.weixin_bot_token}",
                "X-WECHAT-UIN": base64.b64encode(
                    str(random.getrandbits(32)).encode("ascii")
                ).decode(),
            }
            body = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": settings.weixin_user_id,
                    "client_id": str(uuid.uuid4()),
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": settings.weixin_context_token,
                    "item_list": [
                        {"type": 1, "text_item": {"text": text}}
                    ],
                },
                "base_info": {
                    "channel_version": "2.4.10",
                    "bot_agent": "AIAssignmentStudio/0.3.0",
                },
            }
            response = await client.post(
                f"{settings.weixin_base_url.rstrip('/')}/ilink/bot/sendmessage",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            if int(data.get("ret", 0)) != 0:
                raise RuntimeError(f"微信发送失败：{data.get('ret')}")
            result["weixin"] = True
    return result


def _weixin_text(payload: dict[str, Any]) -> str:
    title = {
        "assignment_completed": "AI Assignment Studio 任务完成",
        "assignment_incomplete": "AI Assignment Studio 任务未完成",
        "assignment_failed": "AI Assignment Studio 任务失败",
        "assignment_stopped": "AI Assignment Studio 任务已停止",
        "test": "AI Assignment Studio 测试通知",
    }.get(payload.get("event"), "AI Assignment Studio 任务通知")
    submission = {
        "filled_pending_review": "已填写，待人工核对",
        "filled_verified": "已人工核对填写结果",
        "submitted_unverified": "允许提交，结果未验证",
        "submitted_verified": "提交已验证",
        "partial_pending_review": "部分完成，待人工核对",
        "not_submitted": "未提交",
        "submission_unknown": "提交结果未知",
    }.get(payload.get("submission_status"), "未提供")
    return "\n".join(
        [
            title,
            f"账号：{payload.get('account', '')}",
            f"课程：{payload.get('course', '')}",
            f"作业：{payload.get('assignment', '')}",
            f"自动提交：{'是' if payload.get('auto_submit') else '否'}",
            f"提交状态：{submission}",
            f"结果：{str(payload.get('result', ''))[:500]}",
        ]
    )
