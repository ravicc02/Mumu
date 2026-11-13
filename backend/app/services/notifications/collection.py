from __future__ import annotations

from typing import Any

from ...db import utc_now
from ..assignments.availability import assignment_expired
from .delivery import notification_preferences
from .weixin import record_delivery


def ledger_key(account_id: str, academic_year: int) -> str:
    return f"assignment_notifications:{account_id}:{academic_year}"


def eligible(item: dict[str, Any]) -> bool:
    return bool(item.get("submittable")) and item.get("status") in {"unfinished", "未交", "未完成"} and not assignment_expired(item)


def initialize_baseline(database: Any, account_id: str, academic_year: int) -> None:
    key = ledger_key(account_id, academic_year)
    if database.get_setting(key) is None:
        known = database.list_assignments(account_id, academic_year, active_only=False)
        database.set_setting(key, {"seen": [item["id"] for item in known if eligible(item)], "pending": {}})


async def notify_new_assignments(database: Any, settings: Any, account: dict[str, Any], academic_year: int,
                                 verified: list[dict[str, Any]], *, scheduled: bool, sender: Any) -> dict[str, Any]:
    key = ledger_key(account["id"], academic_year)
    state = database.get_setting(key, {"seen": [], "pending": {}})
    seen = set(state.get("seen") or [])
    pending = dict(state.get("pending") or {})
    preferences = notification_preferences(database)
    current = {item["id"]: item for item in verified if eligible(item)}
    discovered = [item for identifier, item in current.items() if identifier not in seen]
    if not preferences["new_assignments_enabled"]:
        pending.clear()
    elif scheduled:
        for item in discovered:
            pending[item["id"]] = {key: item.get(key, "") for key in ("id", "course_name", "title", "deadline_text")}
    for item in verified:
        if not eligible(item):
            pending.pop(item["id"], None)
    state.update(seen=sorted(seen | current.keys()), pending=pending)
    database.set_setting(key, state)
    deliverable = [item for identifier, item in pending.items() if identifier in current]
    if not scheduled or not deliverable or not preferences["new_assignments_enabled"]:
        return {"status": "skipped", "new_count": len(discovered), "pending_count": len(pending)}
    try:
        channels = await sender(settings, {
            "event": "assignments_discovered", "account": account["name"],
            "account_id": account["id"], "academic_year": academic_year, "assignments": deliverable,
        }, preferences=preferences)
        record_delivery(database,result=channels)
        if not any(channels.values()):
            state["last_error"] = "推送通道尚未就绪，等待下次定时采集重试"
            status = "pending"
        else:
            for item in deliverable:
                pending.pop(item["id"], None)
            state.update(last_error=None, last_sent_at=utc_now())
            status = "sent"
    except Exception as error:
        record_delivery(database,error=error)
        state["last_error"] = str(error)
        status = "failed"
    database.set_setting(key, state)
    return {"status": status, "new_count": len(discovered), "pending_count": len(pending)}
