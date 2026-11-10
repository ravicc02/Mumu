from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import Settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, settings: Settings):
        self.path = settings.database_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  platform TEXT NOT NULL,
                  username TEXT,
                  profile_dir TEXT NOT NULL,
                  storage_state_path TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'new',
                  created_at TEXT NOT NULL,
                  last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS assignments (
                  id TEXT PRIMARY KEY,
                  account_id TEXT NOT NULL,
                  platform TEXT NOT NULL,
                  academic_year INTEGER NOT NULL,
                  semester TEXT,
                  course_name TEXT NOT NULL,
                  course_id TEXT,
                  class_id TEXT,
                  title TEXT NOT NULL,
                  status TEXT NOT NULL,
                  submittable INTEGER NOT NULL DEFAULT 0,
                  deadline_text TEXT,
                  task_url TEXT NOT NULL,
                  work_id TEXT,
                  updated_at TEXT NOT NULL,
                  UNIQUE(account_id, platform, course_id, class_id, work_id),
                  FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY,
                  account_id TEXT NOT NULL,
                  assignment_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  auto_submit INTEGER NOT NULL DEFAULT 0,
                  interactive INTEGER NOT NULL DEFAULT 1,
                  progress INTEGER NOT NULL DEFAULT 0,
                  error TEXT,
                  created_at TEXT NOT NULL,
                  started_at TEXT,
                  finished_at TEXT,
                  FOREIGN KEY(account_id) REFERENCES accounts(id),
                  FOREIGN KEY(assignment_id) REFERENCES assignments(id)
                );

                CREATE TABLE IF NOT EXISTS job_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  message TEXT,
                  data_json TEXT,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                  id TEXT PRIMARY KEY,
                  job_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  path TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                  key TEXT PRIMARY KEY,
                  value TEXT,
                  updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_assignments_account
                  ON assignments(account_id, academic_year, status);
                CREATE INDEX IF NOT EXISTS idx_job_events_job
                  ON job_events(job_id, id);
                """
            )
            job_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "interactive" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN interactive INTEGER NOT NULL DEFAULT 1"
                )

    def create_account(self, name: str, platform: str, settings: Settings) -> dict[str, Any]:
        account_id = uuid.uuid4().hex
        profile_dir = settings.profiles_root / account_id
        storage_state_path = profile_dir / "storage-state.json"
        profile_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO accounts(
                  id, name, platform, profile_dir, storage_state_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    name,
                    platform,
                    str(profile_dir),
                    str(storage_state_path),
                    utc_now(),
                ),
            )
        return self.get_account(account_id)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM accounts ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_account(self, account_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
        if not row:
            raise KeyError("账号不存在")
        return dict(row)

    def update_account(self, account_id: str, **fields: Any) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = [*fields.values(), account_id]
        with self.connect() as connection:
            connection.execute(f"UPDATE accounts SET {columns} WHERE id = ?", values)

    def delete_account(self, account_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM jobs WHERE account_id = ?",
                (account_id,),
            )
            connection.execute(
                "DELETE FROM assignments WHERE account_id = ?",
                (account_id,),
            )
            connection.execute(
                "DELETE FROM accounts WHERE id = ?",
                (account_id,),
            )

    def upsert_assignment(
        self,
        account_id: str,
        platform: str,
        assignment: dict[str, Any],
        settings: Settings,
    ) -> str:
        assignment_id = uuid.uuid4().hex
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT id FROM assignments
                WHERE account_id = ? AND platform = ?
                  AND course_id = ? AND class_id = ? AND work_id = ?
                """,
                (
                    account_id,
                    platform,
                    assignment.get("courseId", ""),
                    assignment.get("classId", ""),
                    assignment.get("workId", ""),
                ),
            ).fetchone()
            if existing:
                assignment_id = existing["id"]
                connection.execute(
                    """
                    UPDATE assignments SET
                      academic_year = ?, semester = ?, course_name = ?,
                      title = ?, status = ?, submittable = ?, deadline_text = ?,
                      task_url = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        assignment.get("academicYear", settings_year(settings)),
                        assignment.get("semester"),
                        assignment.get("courseName", ""),
                        assignment.get("title", ""),
                        assignment.get("status", "unknown"),
                        1 if assignment.get("submittable") else 0,
                        assignment.get("deadlineText", ""),
                        assignment.get("taskUrl", ""),
                        utc_now(),
                        assignment_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO assignments(
                      id, account_id, platform, academic_year, semester,
                      course_name, course_id, class_id, title, status,
                      submittable, deadline_text, task_url, work_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        assignment_id,
                        account_id,
                        platform,
                        assignment.get("academicYear", settings_year(settings)),
                        assignment.get("semester"),
                        assignment.get("courseName", ""),
                        assignment.get("courseId", ""),
                        assignment.get("classId", ""),
                        assignment.get("title", ""),
                        assignment.get("status", "unknown"),
                        1 if assignment.get("submittable") else 0,
                        assignment.get("deadlineText", ""),
                        assignment.get("taskUrl", ""),
                        assignment.get("workId", ""),
                        utc_now(),
                    ),
                )
        return assignment_id

    def list_assignments(
        self,
        account_id: str | None = None,
        academic_year: int | None = None,
        submittable_only: bool = False,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        values: list[Any] = []
        if account_id:
            clauses.append("account_id = ?")
            values.append(account_id)
        if academic_year:
            clauses.append("academic_year = ?")
            values.append(academic_year)
        if submittable_only:
            clauses.append("submittable = 1 AND status IN ('unfinished', '未交', '未完成')")
        if status:
            clauses.append("status = ?")
            values.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM assignments
                {where}
                ORDER BY semester DESC, course_name, deadline_text
                """,
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_assignment(self, assignment_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
            ).fetchone()
        if not row:
            raise KeyError("作业不存在")
        return dict(row)

    def create_job(
        self,
        account_id: str,
        assignment_id: str,
        auto_submit: bool,
        interactive: bool = True,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                  id, account_id, assignment_id, status, auto_submit,
                  interactive, created_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    job_id,
                    account_id,
                    assignment_id,
                    int(auto_submit),
                    int(interactive),
                    utc_now(),
                ),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT jobs.*, assignments.title AS assignment_title,
                  assignments.course_name, assignments.task_url
                FROM jobs
                JOIN assignments ON assignments.id = jobs.assignment_id
                WHERE jobs.id = ?
                """,
                (job_id,),
            ).fetchone()
        if not row:
            raise KeyError("任务不存在")
        return dict(row)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT jobs.*, assignments.title AS assignment_title,
                  assignments.course_name
                FROM jobs
                JOIN assignments ON assignments.id = jobs.assignment_id
                ORDER BY jobs.created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def has_active_job(self, account_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM jobs
                WHERE account_id = ?
                  AND status IN ('queued', 'running', 'paused', 'stopping')
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
        return row is not None

    def mark_interrupted_jobs(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'interrupted',
                    error = '应用重启，任务已中断',
                    finished_at = ?
                WHERE status IN ('queued', 'running', 'paused', 'stopping')
                """,
                (utc_now(),),
            )

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = [*fields.values(), job_id]
        with self.connect() as connection:
            connection.execute(f"UPDATE jobs SET {columns} WHERE id = ?", values)

    def add_event(
        self,
        job_id: str,
        kind: str,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        created_at = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO job_events(job_id, kind, message, data_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    message,
                    json.dumps(data or {}, ensure_ascii=False),
                    created_at,
                ),
            )
            event_id = cursor.lastrowid
        return {
            "id": event_id,
            "job_id": job_id,
            "kind": kind,
            "message": message,
            "data": data or {},
            "created_at": created_at,
        }

    def list_events(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "job_id": row["job_id"],
                "kind": row["kind"],
                "message": row["message"],
                "data": json.loads(row["data_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def add_artifact(self, job_id: str, kind: str, path: Path) -> dict[str, Any]:
        artifact_id = uuid.uuid4().hex
        created_at = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts(id, job_id, kind, path, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (artifact_id, job_id, kind, str(path), created_at),
            )
        return {
            "id": artifact_id,
            "job_id": job_id,
            "kind": kind,
            "path": str(path),
            "created_at": created_at,
        }

    def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE job_id = ? ORDER BY created_at",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        serialized = json.dumps(value, ensure_ascii=False)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value = excluded.value,
                  updated_at = excluded.updated_at
                """,
                (key, serialized, utc_now()),
            )


def settings_year(settings: Settings) -> int:
    del settings
    return datetime.now().year
