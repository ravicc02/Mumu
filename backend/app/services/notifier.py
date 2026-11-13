from __future__ import annotations

import base64
import random
import uuid
from typing import Any

import httpx
from ..version import __version__
from .weixin_connector import check_response


DEFAULT_PREFERENCES = {"new_assignments_enabled": False, "completion_enabled": True}


def notification_preferences(database: Any) -> dict[str, bool]:
    stored = database.get_setting("notification_preferences", {}) or {}
    return {key: bool(stored.get(key, value)) for key, value in DEFAULT_PREFERENCES.items()}


async def send_completion_notification(
    settings: Any,
    payload: dict[str, Any],
    *,
    preferences: dict[str, bool] | None = None,
) -> dict[str, bool]:
    result = {"webhook": False, "weixin": False}
    preferences = {**DEFAULT_PREFERENCES, **(preferences or {})}
    event = payload.get("event", "")
    if event == "assignments_discovered" and not preferences["new_assignments_enabled"]:
        return result
    if event.startswith(("assignment_", "learning_")) and not preferences["completion_enabled"]:
        return result
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
                    "bot_agent": f"AIAssignmentStudio/{__version__}",
                },
            }
            response = await client.post(
                f"{settings.weixin_base_url.rstrip('/')}/ilink/bot/sendmessage",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            check_response(data,"微信发送失败")
            result["weixin"] = True
    return result


def _brief_result(payload: dict[str, Any]) -> str:
    result = str(payload.get("result") or "").strip()
    if "核验失败" in result or "未通过核验" in result:
        return "部分答案未通过页面核验，请打开任务处理"
    if any(word in result.lower() for word in ("api", "429", "timeout", "network", "模型", "网络")):
        return "模型或网络请求异常，请查看任务详情"
    if any(word in result for word in ("登录", "授权", "Cookie")):
        return "登录已失效，请重新授权"
    if payload.get("event") == "assignment_needs_intervention":
        return "已填写，等待人工审核"  # Saving is reported only when the task confirms it.
    if len(result) > 90 or "https://" in result or "http://" in result:
        return "请查看任务详情处理"
    return result.splitlines()[0] if result else "请查看任务详情"


def _weixin_text(payload: dict[str, Any]) -> str:
    event = str(payload.get("event", ""))
    account = str(payload.get("account", "")).strip()
    if event == "assignments_discovered":
        items = payload.get("assignments") or []
        lines = [f"新作业 · {account}（{len(items)}）"]
        for item in items[:5]:
            lines.append(f"{str(item.get('course_name', ''))[:40]}｜{str(item.get('title', ''))[:70]}")
            if item.get("deadline_text"):
                lines.append(f"截止 {str(item['deadline_text'])[:50]}")
        if len(items) > 5:
            lines.append(f"另有 {len(items) - 5} 个作业")
        return "\n".join(lines)
    if event.startswith("learning_"):
        status = {"completed": "网课已完成", "stopped": "网课已停止", "interrupted": "网课中断",
                  "incomplete": "网课未完成", "awaiting_user": "网课待处理"}.get(payload.get("status"), "网课结果")
        result = "平台进度已核验" if payload.get("status") == "completed" else _brief_result(payload)
        return "\n".join([f"{status} · {account}", str(payload.get("course", ""))[:100], result])
    title = {
        "assignment_completed": "已完成", "assignment_incomplete": "未完成", "assignment_failed": "中断",
        "assignment_interrupted": "中断", "assignment_needs_intervention": "待审核", "assignment_stopped": "已停止",
        "test": "测试通知",
    }.get(event, "任务结果")
    result = "已完成并提交" if payload.get("submission_status") == "submitted_verified" else _brief_result(payload)
    subject = "｜".join(str(payload.get(key, ""))[:80] for key in ("course", "assignment") if payload.get(key))
    return "\n".join(line for line in (f"{title} · {account}", subject, result) if line)


def notification_examples() -> dict[str, str]:
    return {
        "new_assignment": _weixin_text({"event": "assignments_discovered", "account": "我的账号",
            "assignments": [{"course_name": "软件工程", "title": "实验四", "deadline_text": "11-30 10:09"}]}),
        "completion": _weixin_text({"event": "assignment_needs_intervention", "account": "我的账号",
            "course": "软件工程", "assignment": "实验四"}),
    }
