from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from ...db import utc_now
from ..platforms.catalog import platform_spec
from .readiness import direct_answerable, execution_block


GROUP_TIMEZONE = ZoneInfo("Asia/Shanghai")
GROUP_SCAN_INTERVAL_SECONDS = 60
ALL_DAY_WINDOW = {
    "days": [1, 2, 3, 4, 5, 6, 7],
    "start": "00:00",
    "end": "23:59",
}


def _load_list(value) -> list:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _account_ids(value) -> list[str]:
    return list(dict.fromkeys(str(item) for item in _load_list(value) if str(item)))


def _course_filters(value) -> list[str]:
    return list(dict.fromkeys(
        str(item).strip()
        for item in _load_list(value)
        if str(item).strip()
    ))


def _windows(value) -> list[dict]:
    result = []
    for item in _load_list(value):
        if not isinstance(item, dict):
            continue
        days = sorted({
            int(day)
            for day in item.get("days") or []
            if str(day).isdigit() and 1 <= int(day) <= 7
        })
        start = str(item.get("start") or "")
        end = str(item.get("end") or "")
        if days and start and end:
            result.append({"days": days, "start": start, "end": end})
    return result


def _group_from_row(row) -> dict:
    return {
        **dict(row),
        "account_ids": _account_ids(row["account_ids_json"]),
        "course_filters": _course_filters(row["course_filters_json"]),
        "windows": _windows(row["windows_json"]),
        "auto_submit": bool(row["auto_submit"]),
        "enabled": bool(row["enabled"]),
        "baseline_ready": bool(row["baseline_ready"]),
    }


def _clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":", 1))
    return time(hour, minute)


def window_open(windows: list[dict], now: datetime | None = None) -> bool:
    now = now or datetime.now(GROUP_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=GROUP_TIMEZONE)
    else:
        now = now.astimezone(GROUP_TIMEZONE)
    if not windows:
        return True
    weekday = now.isoweekday()
    current = now.time().replace(tzinfo=None)
    for window in windows:
        if weekday not in window.get("days", []):
            continue
        start = _clock(window["start"])
        end = _clock(window["end"])
        if start <= end and start <= current <= end:
            return True
        if start > end and (current >= start or current <= end):
            return True
    return False


def scan_due(group: dict, now: datetime | None = None) -> bool:
    if not group.get("enabled"):
        return False
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if not window_open(group.get("windows") or [], now):
        return False
    value = group.get("last_scan_at")
    if not value:
        return True
    try:
        previous = datetime.fromisoformat(value)
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (now - previous).total_seconds() >= GROUP_SCAN_INTERVAL_SECONDS


def _validate_accounts(database, account_ids: list[str]) -> list[dict]:
    ids = list(dict.fromkeys(account_ids))
    if not ids:
        raise ValueError("请选择至少一个账号")
    accounts = []
    for identifier in ids:
        try:
            account = database.get_account(identifier)
        except KeyError as error:
            raise ValueError("账号已不存在，请刷新后重试") from error
        if not platform_spec(account["platform"])["assignments"]:
            raise ValueError(f"{account['name']} 不支持作业自动处理")
        accounts.append(account)
    return accounts


def _public_group(database, group: dict) -> dict:
    accounts = []
    for identifier in group["account_ids"]:
        try:
            account = database.get_account(identifier)
        except KeyError:
            continue
        accounts.append({
            "id": account["id"],
            "name": account["name"],
            "platform": account["platform"],
            "status": account["status"],
        })
    with database.connect() as connection:
        counts = {
            row["state"]: int(row["count"])
            for row in connection.execute(
                "SELECT state,COUNT(*) AS count FROM assignment_auto_group_items "
                "WHERE group_id=? GROUP BY state",
                (group["id"],),
            )
        }
    return {
        **{key: value for key, value in group.items() if not key.endswith("_json")},
        "accounts": accounts,
        "scan_interval_seconds": GROUP_SCAN_INTERVAL_SECONDS,
        "pending_count": counts.get("pending", 0),
        "queued_count": counts.get("queued", 0),
        "skipped_count": counts.get("skipped", 0),
        "failed_count": counts.get("failed", 0),
    }


def get_group(database, identifier: str) -> dict:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM assignment_auto_groups WHERE id=?",
            (identifier,),
        ).fetchone()
    if not row:
        raise KeyError("自动处理分组不存在")
    return _group_from_row(row)


def public_group(database, identifier: str) -> dict:
    return _public_group(database, get_group(database, identifier))


def list_groups(database) -> list[dict]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM assignment_auto_groups ORDER BY created_at,id"
        ).fetchall()
    return [_public_group(database, _group_from_row(row)) for row in rows]


def create_group(database, values: dict) -> dict:
    _validate_accounts(database, values["account_ids"])
    now = utc_now()
    identifier = uuid.uuid4().hex
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO assignment_auto_groups(
                id,name,account_ids_json,course_filters_json,windows_json,
                auto_submit,enabled,baseline_ready,last_scan_at,last_status,
                last_error,created_at,updated_at
              ) VALUES(?,?,?,?,?,?,?,0,NULL,'waiting','',?,?)""",
            (
                identifier,
                values["name"].strip(),
                json.dumps(values["account_ids"], ensure_ascii=False),
                json.dumps(values["course_filters"], ensure_ascii=False),
                json.dumps(values["windows"], ensure_ascii=False),
                int(values.get("auto_submit", False)),
                int(values.get("enabled", True)),
                now,
                now,
            ),
        )
    return get_group(database, identifier)


def update_group(database, identifier: str, values: dict) -> dict:
    current = get_group(database, identifier)
    merged = {
        **current,
        **{key: value for key, value in values.items() if value is not None},
    }
    _validate_accounts(database, merged["account_ids"])
    scope_changed = any(
        merged[key] != current[key]
        for key in ("account_ids", "course_filters", "windows")
    )
    reenabled = bool(merged["enabled"]) and not current["enabled"]
    now = utc_now()
    with database.connect() as connection:
        if scope_changed or reenabled:
            connection.execute(
                "DELETE FROM assignment_auto_group_items WHERE group_id=?",
                (identifier,),
            )
        connection.execute(
            """UPDATE assignment_auto_groups SET
              name=?,account_ids_json=?,course_filters_json=?,windows_json=?,
              auto_submit=?,enabled=?,baseline_ready=?,updated_at=?
              WHERE id=?""",
            (
                merged["name"].strip(),
                json.dumps(merged["account_ids"], ensure_ascii=False),
                json.dumps(merged["course_filters"], ensure_ascii=False),
                json.dumps(merged["windows"], ensure_ascii=False),
                int(merged["auto_submit"]),
                int(merged["enabled"]),
                0 if scope_changed or reenabled else int(merged["baseline_ready"]),
                now,
                identifier,
            ),
        )
    return get_group(database, identifier)


def delete_group(database, identifier: str) -> None:
    get_group(database, identifier)
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM assignment_auto_groups WHERE id=?",
            (identifier,),
        )


def course_catalog(database, account_ids: list[str]) -> dict:
    accounts = _validate_accounts(database, account_ids)
    placeholders = ",".join("?" for _ in accounts)
    with database.connect() as connection:
        rows = connection.execute(
            f"""SELECT course_name,GROUP_CONCAT(DISTINCT account_id) AS account_ids,
              COUNT(DISTINCT account_id) AS account_count
              FROM assignments
              WHERE active=1 AND account_id IN ({placeholders})
              GROUP BY course_name ORDER BY course_name""",
            tuple(account["id"] for account in accounts),
        ).fetchall()
    courses = [{
        "name": row["course_name"],
        "account_ids": str(row["account_ids"] or "").split(",") if row["account_ids"] else [],
        "account_count": int(row["account_count"] or 0),
        "common": int(row["account_count"] or 0) == len(accounts),
    } for row in rows if row["course_name"]]
    return {"courses": courses, "accounts": [
        {"id": account["id"], "name": account["name"], "status": account["status"]}
        for account in accounts
    ]}


def record_discovery(database, group: dict, assignments: list[dict], *, complete: bool) -> None:
    filters = set(group.get("course_filters") or [])
    now = utc_now()
    with database.connect() as connection:
        current = connection.execute(
            "SELECT baseline_ready FROM assignment_auto_groups WHERE id=?",
            (group["id"],),
        ).fetchone()
        baseline_ready = bool(current and current["baseline_ready"])
        for assignment in assignments:
            course = str(assignment.get("course_name") or "")
            if filters and course not in filters:
                continue
            reason = ""
            state = "existing" if not baseline_ready else "pending"
            if baseline_ready:
                if not assignment.get("active") or not assignment.get("submittable"):
                    state, reason = "skipped", "作业已完成或当前不可提交"
                elif not direct_answerable(assignment):
                    state, reason = "skipped", execution_block(assignment) or "仅自动处理选择题和填空题"
            connection.execute(
                """INSERT OR IGNORE INTO assignment_auto_group_items(
                  group_id,assignment_id,account_id,state,reason,job_id,created_at,updated_at
                ) VALUES(?,?,?,?,?,NULL,?,?)""",
                (
                    group["id"],
                    assignment["id"],
                    assignment["account_id"],
                    state,
                    reason,
                    now,
                    now,
                ),
            )
        if complete:
            connection.execute(
                "UPDATE assignment_auto_groups SET baseline_ready=1,updated_at=? WHERE id=?",
                (now, group["id"]),
            )


def pending_items(database) -> list[dict]:
    with database.connect() as connection:
        rows = connection.execute(
            """SELECT items.*,groups.name AS group_name,groups.auto_submit,
              assignments.title,assignments.course_name,assignments.status,
              assignments.submittable,assignments.active,assignments.assessment_json
              FROM assignment_auto_group_items AS items
              JOIN assignment_auto_groups AS groups ON groups.id=items.group_id
              JOIN assignments ON assignments.id=items.assignment_id
              WHERE items.state='pending' AND groups.enabled=1
              ORDER BY items.created_at,items.assignment_id"""
        ).fetchall()
    return [dict(row) for row in rows]


def mark_item(database, group_id: str, assignment_id: str, *, state: str, reason: str = "", job_id: str | None = None) -> None:
    with database.connect() as connection:
        connection.execute(
            """UPDATE assignment_auto_group_items SET state=?,reason=?,job_id=COALESCE(?,job_id),
              updated_at=? WHERE group_id=? AND assignment_id=?""",
            (state, reason, job_id, utc_now(), group_id, assignment_id),
        )


def mark_scan_started(database, identifier: str) -> None:
    now = utc_now()
    with database.connect() as connection:
        connection.execute(
            """UPDATE assignment_auto_groups SET last_scan_at=?,last_status='running',
              last_error='',updated_at=? WHERE id=?""",
            (now, now, identifier),
        )


def mark_scan_finished(database, identifier: str, *, status: str, error: str = "") -> None:
    now = utc_now()
    with database.connect() as connection:
        connection.execute(
            """UPDATE assignment_auto_groups SET last_status=?,last_error=?,updated_at=?
              WHERE id=?""",
            (status, str(error)[:500], now, identifier),
        )


def migrate_legacy_schedules(database) -> None:
    accounts = database.list_accounts()
    legacy = [
        account for account in accounts
        if account.get("auto_collect_enabled") or account.get("auto_execute_enabled")
    ]
    if not legacy:
        return
    executable = [account for account in legacy if account.get("auto_execute_enabled")]
    if executable and not list_groups(database):
        from ...preferences import load_preferences
        group = create_group(database, {
            "name": "原有自动处理",
            "account_ids": [account["id"] for account in executable],
            "course_filters": [],
            "windows": [ALL_DAY_WINDOW],
            "auto_submit": load_preferences(database).assignments.auto_submit,
            "enabled": True,
        })
        assignments = []
        for account in executable:
            assignments.extend(database.list_assignments(
                account_id=account["id"],
                submittable_only=False,
            ))
        record_discovery(database, group, assignments, complete=True)
    for account in legacy:
        database.update_account(
            account["id"],
            auto_collect_enabled=0,
            auto_execute_enabled=0,
        )
