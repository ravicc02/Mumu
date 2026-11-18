"""数据目录布局升级：把旧版扁平结构归入 accounts/<账号>/。

旧结构：
    profiles/<账号>/            Chrome 用户数据目录
    task-workspaces/<类型>/<尝试>/
    artifacts/<任务>/           或 artifacts/learning/<学习记录>/
    attendance-assets/<账号>/

新结构：
    accounts/<账号>/browser/        Chrome 用户数据目录
    accounts/<账号>/tasks/<类型>/<尝试>/
    accounts/<账号>/artifacts/<任务>/
    accounts/<账号>/attendance/

约束：
- 幂等：目标已存在时保留目标，不覆盖。
- 保守：无法判定归属的目录留在原地，不删除。
- 同步更新数据库中的绝对路径，避免产物引用失效。
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .config import DATABASE_FILENAME

logger = logging.getLogger("mumu.data_layout")

LEGACY_DIRECTORIES = ("profiles", "task-workspaces", "artifacts", "attendance-assets")


def _move(src: Path, dst: Path) -> bool:
    """把 src 整体移到 dst。目标已存在时保留目标并返回 False。"""
    if not src.exists() or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(dst))
    except OSError as error:
        logger.warning(
            "数据目录移动失败",
            extra={
                "diagnostic_code": "mumu.data_layout.move_failed",
                "diagnostic_data": {"source": str(src), "target": str(dst), "error": str(error)},
            },
        )
        return False
    return True


def _task_account_id(attempt_dir: Path) -> str:
    manifest = attempt_dir / "manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    account = payload.get("account") if isinstance(payload, dict) else None
    if isinstance(account, dict):
        return str(account.get("id") or "")
    return ""


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def upgrade_layout(root: Path) -> dict[str, int]:
    """把旧布局迁移到 accounts/<账号>/。返回各类移动计数。"""
    root = Path(root).resolve()
    counters: dict[str, int] = {"accounts": 0, "tasks": 0, "artifacts": 0, "attendance": 0}
    legacy = {name: root / name for name in LEGACY_DIRECTORIES}
    if not any(path.exists() for path in legacy.values()):
        return counters
    database_path = root / DATABASE_FILENAME
    if not database_path.is_file():
        return counters

    accounts_root = root / "accounts"
    artifacts_root = legacy["artifacts"]

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        accounts = list(
            connection.execute("SELECT id, profile_dir, storage_state_path FROM accounts")
        )
        for row in accounts:
            account_id = str(row["id"])
            browser = accounts_root / account_id / "browser"
            if _move(legacy["profiles"] / account_id, browser):
                counters["accounts"] += 1
            if browser.exists():
                profile_dir = str(browser)
                storage_state = str(browser / "storage-state.json")
                if row["profile_dir"] != profile_dir or row["storage_state_path"] != storage_state:
                    connection.execute(
                        "UPDATE accounts SET profile_dir=?, storage_state_path=? WHERE id=?",
                        (profile_dir, storage_state, account_id),
                    )

        attendance_root = legacy["attendance-assets"]
        if attendance_root.exists():
            for entry in attendance_root.iterdir():
                if entry.is_dir() and _move(entry, accounts_root / entry.name / "attendance"):
                    counters["attendance"] += 1

        tasks_root = legacy["task-workspaces"]
        if tasks_root.exists():
            for kind_dir in tasks_root.iterdir():
                if not kind_dir.is_dir():
                    continue
                for attempt in kind_dir.iterdir():
                    if not attempt.is_dir():
                        continue
                    account_id = _task_account_id(attempt)
                    if not account_id:
                        continue
                    destination = accounts_root / account_id / "tasks" / kind_dir.name / attempt.name
                    if _move(attempt, destination):
                        counters["tasks"] += 1

        job_account: dict[str, str] = {}
        run_account: dict[str, str] = {}
        if _table_exists(connection, "jobs"):
            job_account = {
                str(row["id"]): str(row["account_id"])
                for row in connection.execute("SELECT id, account_id FROM jobs")
            }
        if _table_exists(connection, "learning_runs"):
            run_account = {
                str(row["id"]): str(row["account_id"])
                for row in connection.execute("SELECT id, account_id FROM learning_runs")
            }

        if artifacts_root.exists():
            for entry in artifacts_root.iterdir():
                if not entry.is_dir():
                    continue
                if entry.name == "learning":
                    for run_dir in entry.iterdir():
                        if not run_dir.is_dir():
                            continue
                        account_id = run_account.get(run_dir.name, "")
                        if not account_id:
                            continue
                        destination = accounts_root / account_id / "artifacts" / run_dir.name
                        if _move(run_dir, destination):
                            counters["artifacts"] += 1
                    continue
                account_id = job_account.get(entry.name) or run_account.get(entry.name)
                if not account_id:
                    continue
                destination = accounts_root / account_id / "artifacts" / entry.name
                if _move(entry, destination):
                    counters["artifacts"] += 1

        for table in ("artifacts", "learning_artifacts"):
            if not _table_exists(connection, table):
                continue
            for row in list(connection.execute(f"SELECT id, path FROM {table}")):
                original = Path(str(row["path"]))
                try:
                    relative = original.relative_to(artifacts_root)
                except ValueError:
                    continue
                parts = relative.parts
                if not parts:
                    continue
                if parts[0] == "learning":
                    if len(parts) < 2:
                        continue
                    account_id = run_account.get(parts[1], "")
                    relative = Path(*parts[1:])
                else:
                    account_id = job_account.get(parts[0], "")
                if not account_id:
                    continue
                updated = accounts_root / account_id / "artifacts" / relative
                if str(updated) != str(original):
                    connection.execute(
                        f"UPDATE {table} SET path=? WHERE id=?", (str(updated), row["id"])
                    )
        connection.commit()

    for name, path in legacy.items():
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass

    if any(counters.values()):
        logger.info(
            "数据目录布局已升级",
            extra={
                "diagnostic_code": "mumu.data_layout.upgraded",
                "diagnostic_data": {**counters, "root": str(root)},
            },
        )
    return counters
