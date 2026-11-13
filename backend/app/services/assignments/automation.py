from __future__ import annotations

import json

from ...db import utc_now
from ...preferences import load_preferences
from ..notifications.collection import eligible
from ..platforms.catalog import platform_spec


def save_preferences(database, update):
    current = load_preferences(database).model_dump()
    for section in ("assignments", "learning", "system"):
        value = getattr(update, section)
        if value is not None:
            current[section] = value.model_dump()
    schedules = update.schedules or []
    if len({item.account_id for item in schedules}) != len(schedules):
        raise ValueError("同一账号不能重复配置")
    now = utc_now()
    with database.connect() as connection:
        for schedule in schedules:
            row = connection.execute("SELECT * FROM accounts WHERE id = ?", (schedule.account_id,)).fetchone()
            if row is None:
                raise ValueError("账号已不存在，请刷新设置")
            account = dict(row)
            if schedule.enabled and not platform_spec(account["platform"])["assignments"]:
                raise ValueError("该平台尚未支持作业自动检测")
            enabling = not account["auto_collect_enabled"] or (schedule.auto_execute and not account["auto_execute_enabled"])
            if schedule.enabled and enabling and account["status"] != "ready":
                raise ValueError(f"{account['name']} 尚未授权，请先完成授权")
            baseline = account["automation_baseline_ready"]
            if schedule.auto_execute and not account["auto_execute_enabled"]:
                known = connection.execute("SELECT id FROM assignments WHERE account_id = ?", (schedule.account_id,)).fetchall()
                for item in known:
                    connection.execute("INSERT OR IGNORE INTO automatic_assignments VALUES (?, ?, 'existing', '', NULL, ?, ?)",
                                       (item["id"], schedule.account_id, now, now))
                baseline = int(bool(known) or account["last_collection_status"] == "success")
            if not schedule.auto_execute:
                connection.execute("UPDATE automatic_assignments SET state = 'cancelled', reason = '自动完成已关闭', updated_at = ? "
                                   "WHERE account_id = ? AND state = 'pending'", (now, schedule.account_id))
            connection.execute("""UPDATE accounts SET auto_collect_enabled = ?, auto_execute_enabled = ?,
                auto_collect_interval_seconds = ?, auto_collect_interval_minutes = ?, auto_collect_interval_value = ?,
                auto_collect_interval_unit = ?, automation_baseline_ready = ?,
                last_collection_at = COALESCE(last_collection_at, ?) WHERE id = ?""",
                (int(schedule.enabled), int(schedule.auto_execute), schedule.seconds, max(1, (schedule.seconds + 59) // 60),
                 schedule.interval_value, schedule.interval_unit, baseline, now, schedule.account_id))
        connection.execute("""INSERT INTO app_settings(key, value, updated_at) VALUES ('preferences', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (json.dumps(current), now))
    return current


def record_discovery(database, account_id, verified, *, scheduled, complete=True):
    if not scheduled:
        return
    account = database.get_account(account_id)
    if not account["auto_collect_enabled"] or not account["auto_execute_enabled"]:
        return
    now = utc_now()
    with database.connect() as connection:
        for assignment in verified:
            attempted = connection.execute("SELECT 1 FROM jobs WHERE assignment_id = ? LIMIT 1", (assignment["id"],)).fetchone()
            state = "pending" if account["automation_baseline_ready"] and eligible(assignment) and not attempted else "existing"
            connection.execute("INSERT OR IGNORE INTO automatic_assignments VALUES (?, ?, ?, '', NULL, ?, ?)",
                               (assignment["id"], account_id, state, now, now))
        if complete:
            connection.execute("UPDATE accounts SET automation_baseline_ready = 1 WHERE id = ?", (account_id,))


def pending_assignments(database):
    with database.connect() as connection:
        return [dict(row) for row in connection.execute("""SELECT automatic_assignments.*, assignments.active,
            assignments.status AS assignment_status, assignments.submittable
            FROM automatic_assignments JOIN assignments ON assignments.id = assignment_id
            WHERE state = 'pending' ORDER BY automatic_assignments.created_at, assignment_id""")]


def mark_pending(database, assignment_id, *, reason, state="pending"):
    with database.connect() as connection:
        connection.execute("UPDATE automatic_assignments SET reason = ?, state = ?, updated_at = ? "
                           "WHERE assignment_id = ? AND state = 'pending' AND (reason != ? OR state != ?)",
                           (reason, state, utc_now(), assignment_id, reason, state))


def automation_status(database, account_id):
    with database.connect() as connection:
        rows = connection.execute("SELECT reason FROM automatic_assignments WHERE account_id = ? AND state = 'pending' "
                                  "ORDER BY created_at", (account_id,)).fetchall()
        failures = connection.execute("SELECT reason FROM automatic_assignments WHERE account_id = ? AND state = 'failed' "
                                      "ORDER BY updated_at DESC", (account_id,)).fetchall()
    return {"pending": len(rows), "reason": rows[0]["reason"] if rows else "",
            "failed": len(failures), "last_error": failures[0]["reason"] if failures else ""}
