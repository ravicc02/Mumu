from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import Settings


ACTIVE_JOB_STATUSES = {
    "queued",
    "running",
    "paused",
    "stopping",
    "needs_intervention",
}
JOB_TRANSITIONS = {
    "queued": {"running", "paused", "stopping", "stopped", "failed", "interrupted"},
    "running": {
        "running",
        "paused",
        "stopping",
        "stopped",
        "completed",
        "incomplete",
        "needs_intervention",
        "failed",
        "interrupted",
    },
    "paused": {"queued", "running", "stopping", "stopped", "failed", "interrupted"},
    "stopping": {"stopped", "failed", "interrupted"},
    "stopped": set(),
    "completed": set(),
    "incomplete": set(),
    "failed": set(),
    "needs_intervention": {"stopped", "failed", "interrupted"},
    "interrupted": set(),
}


class ActiveJobConflict(RuntimeError):
    pass


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
                  last_login_at TEXT,
                  last_login_error TEXT,
                  auto_collect_enabled INTEGER NOT NULL DEFAULT 0,
                  auto_collect_interval_minutes INTEGER NOT NULL DEFAULT 360,
                  last_collection_at TEXT,
                  last_collection_status TEXT NOT NULL DEFAULT 'never',
                  last_collection_error TEXT,
                  last_collection_count INTEGER NOT NULL DEFAULT 0,
                  last_collection_run_id TEXT
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
                  active INTEGER NOT NULL DEFAULT 1,
                  last_seen_run_id TEXT,
                  updated_at TEXT NOT NULL,
                  UNIQUE(account_id, platform, course_id, class_id, work_id),
                  FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY,
                  account_id TEXT NOT NULL,
                  assignment_id TEXT NOT NULL,
                  parent_job_id TEXT,
                  attempt INTEGER NOT NULL DEFAULT 1,
                  status TEXT NOT NULL,
                  submission_status TEXT NOT NULL DEFAULT 'not_started',
                  auto_submit INTEGER NOT NULL DEFAULT 0,
                  interactive INTEGER NOT NULL DEFAULT 1,
                  progress INTEGER NOT NULL DEFAULT 0,
                  error TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_heartbeat_at TEXT,
                  paused_from_status TEXT,
                  verified_at TEXT,
                  verification_note TEXT,
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

                CREATE TABLE IF NOT EXISTS collection_runs (
                  id TEXT PRIMARY KEY,
                  account_id TEXT NOT NULL,
                  academic_year INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  progress INTEGER NOT NULL DEFAULT 0,
                  current_course TEXT,
                  total_courses INTEGER NOT NULL DEFAULT 0,
                  processed_courses INTEGER NOT NULL DEFAULT 0,
                  failed_courses INTEGER NOT NULL DEFAULT 0,
                  assignment_count INTEGER NOT NULL DEFAULT 0,
                  submittable_count INTEGER NOT NULL DEFAULT 0,
                  error TEXT,
                  errors_json TEXT,
                  started_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  finished_at TEXT,
                  FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_assignments_account
                  ON assignments(account_id, academic_year, status);
                CREATE INDEX IF NOT EXISTS idx_job_events_job
                  ON job_events(job_id, id);
                CREATE INDEX IF NOT EXISTS idx_collection_runs_account
                  ON collection_runs(account_id, started_at);
                DROP INDEX IF EXISTS idx_jobs_active_assignment;
                CREATE UNIQUE INDEX idx_jobs_active_assignment
                  ON jobs(assignment_id)
                  WHERE status IN (
                    'queued',
                    'running',
                    'paused',
                    'stopping',
                    'needs_intervention'
                  );
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
            if "parent_job_id" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN parent_job_id TEXT"
                )
            if "attempt" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1"
                )
            if "submission_status" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN submission_status "
                    "TEXT NOT NULL DEFAULT 'not_started'"
                )
            if "updated_at" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN updated_at TEXT")
            if "last_heartbeat_at" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN last_heartbeat_at TEXT"
                )
            if "paused_from_status" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN paused_from_status TEXT"
                )
            if "verified_at" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN verified_at TEXT"
                )
            if "verification_note" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN verification_note TEXT"
                )
            connection.execute(
                """
                UPDATE jobs
                SET updated_at = COALESCE(updated_at, finished_at, started_at, created_at)
                """
            )
            connection.execute(
                """
                UPDATE jobs
                SET submission_status = CASE
                  WHEN status = 'completed' AND auto_submit = 1
                    THEN 'submitted_unverified'
                  WHEN status = 'completed'
                    THEN 'filled_pending_review'
                  WHEN status IN ('incomplete', 'needs_intervention')
                    THEN 'partial_pending_review'
                  WHEN status IN ('failed', 'stopped', 'interrupted')
                    THEN 'not_submitted'
                  ELSE 'not_started'
                END
                WHERE submission_status = 'not_started'
                """
            )
            missing_interruptions = connection.execute(
                """
                SELECT jobs.id, jobs.error, jobs.finished_at, jobs.updated_at
                FROM jobs
                WHERE jobs.status = 'interrupted'
                  AND NOT EXISTS (
                    SELECT 1 FROM job_events
                    WHERE job_events.job_id = jobs.id
                      AND job_events.kind = 'interrupted'
                  )
                """
            ).fetchall()
            for row in missing_interruptions:
                connection.execute(
                    """
                    INSERT INTO job_events(job_id, kind, message, data_json, created_at)
                    VALUES (?, 'interrupted', ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["error"] or "任务已中断",
                        json.dumps(
                            {"backfilled": True},
                            ensure_ascii=False,
                        ),
                        row["finished_at"] or row["updated_at"],
                    ),
                )
            connection.execute(
                """
                UPDATE accounts
                SET status = 'login_error'
                WHERE status = 'authorizing'
                """
            )
            account_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(accounts)"
                ).fetchall()
            }
            if "last_login_error" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN last_login_error TEXT"
                )
            if "auto_collect_enabled" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN "
                    "auto_collect_enabled INTEGER NOT NULL DEFAULT 0"
                )
            if "auto_collect_interval_minutes" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN "
                    "auto_collect_interval_minutes INTEGER NOT NULL DEFAULT 360"
                )
            if "last_collection_at" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN last_collection_at TEXT"
                )
            if "last_collection_status" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN "
                    "last_collection_status TEXT NOT NULL DEFAULT 'never'"
                )
            if "last_collection_error" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN last_collection_error TEXT"
                )
            if "last_collection_count" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN "
                    "last_collection_count INTEGER NOT NULL DEFAULT 0"
                )
            if "last_collection_run_id" not in account_columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN last_collection_run_id TEXT"
                )
            assignment_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(assignments)"
                ).fetchall()
            }
            if "active" not in assignment_columns:
                connection.execute(
                    "ALTER TABLE assignments ADD COLUMN "
                    "active INTEGER NOT NULL DEFAULT 1"
                )
            if "last_seen_run_id" not in assignment_columns:
                connection.execute(
                    "ALTER TABLE assignments ADD COLUMN last_seen_run_id TEXT"
                )
            interrupted_runs = connection.execute(
                """
                SELECT id, account_id
                FROM collection_runs
                WHERE status = 'running'
                """
            ).fetchall()
            interrupted_at = utc_now()
            for row in interrupted_runs:
                connection.execute(
                    """
                    UPDATE collection_runs
                    SET status = 'failed',
                        error = '应用重启，采集已中断',
                        updated_at = ?,
                        finished_at = ?
                    WHERE id = ?
                    """,
                    (interrupted_at, interrupted_at, row["id"]),
                )
                connection.execute(
                    """
                    UPDATE accounts
                    SET last_collection_status = 'error',
                        last_collection_error = '应用重启，采集已中断',
                        last_collection_at = ?
                    WHERE id = ?
                    """,
                    (interrupted_at, row["account_id"]),
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
        run_id: str | None = None,
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
                      task_url = ?, active = 1, last_seen_run_id = ?, updated_at = ?
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
                        run_id,
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
                      submittable, deadline_text, task_url, work_id,
                      active, last_seen_run_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        1,
                        run_id,
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
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        clauses = []
        values: list[Any] = []
        if active_only:
            clauses.append("active = 1")
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

    def finalize_assignment_snapshot(
        self,
        account_id: str,
        academic_year: int,
        run_id: str,
        failed_course_ids: set[str] | None = None,
    ) -> None:
        failed = {
            str(course_id)
            for course_id in (failed_course_ids or set())
            if str(course_id)
        }
        clauses = [
            "account_id = ?",
            "academic_year = ?",
            "(last_seen_run_id IS NULL OR last_seen_run_id != ?)",
        ]
        values: list[Any] = [account_id, academic_year, run_id]
        if failed:
            placeholders = ", ".join("?" for _ in failed)
            clauses.append(f"COALESCE(course_id, '') NOT IN ({placeholders})")
            values.extend(sorted(failed))
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE assignments
                SET active = 0, updated_at = ?
                WHERE {' AND '.join(clauses)}
                """,
                [utc_now(), *values],
            )

    def create_collection_run(
        self,
        account_id: str,
        academic_year: int,
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        started_at = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO collection_runs(
                  id, account_id, academic_year, status, progress,
                  started_at, updated_at
                ) VALUES (?, ?, ?, 'running', 0, ?, ?)
                """,
                (run_id, account_id, academic_year, started_at, started_at),
            )
        return self.get_collection_run(run_id)

    def get_collection_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM collection_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            raise KeyError("采集记录不存在")
        result = dict(row)
        errors_json = result.pop("errors_json", None)
        try:
            result["errors"] = json.loads(errors_json or "[]")
        except json.JSONDecodeError:
            result["errors"] = []
        return result

    def list_collection_runs(
        self,
        account_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses = ""
        values: list[Any] = []
        if account_id:
            clauses = "WHERE account_id = ?"
            values.append(account_id)
        values.append(max(1, min(int(limit), 200)))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM collection_runs
                {clauses}
                ORDER BY started_at DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            errors_json = result.pop("errors_json", None)
            try:
                result["errors"] = json.loads(errors_json or "[]")
            except json.JSONDecodeError:
                result["errors"] = []
            results.append(result)
        return results

    def update_collection_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        if "errors" in fields:
            fields["errors_json"] = json.dumps(
                fields.pop("errors"),
                ensure_ascii=False,
            )
        fields.setdefault("updated_at", utc_now())
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = [*fields.values(), run_id]
        with self.connect() as connection:
            connection.execute(
                f"UPDATE collection_runs SET {columns} WHERE id = ?",
                values,
            )

    def finish_collection_run(
        self,
        run_id: str,
        status: str,
        *,
        total_courses: int,
        processed_courses: int,
        failed_courses: int,
        assignment_count: int,
        submittable_count: int,
        error: str | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        finished_at = utc_now()
        account_status = {
            "success": "success",
            "partial": "partial",
            "failed": "error",
        }.get(status, status)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT account_id FROM collection_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                raise KeyError("采集记录不存在")
            connection.execute(
                """
                UPDATE collection_runs
                SET status = ?, progress = ?, current_course = NULL,
                    total_courses = ?, processed_courses = ?,
                    failed_courses = ?, assignment_count = ?,
                    submittable_count = ?, error = ?, errors_json = ?,
                    updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    100 if status in {"success", "partial"} else 0,
                    total_courses,
                    processed_courses,
                    failed_courses,
                    assignment_count,
                    submittable_count,
                    error,
                    json.dumps(errors or [], ensure_ascii=False),
                    finished_at,
                    finished_at,
                    run_id,
                ),
            )
            connection.execute(
                """
                UPDATE accounts
                SET last_collection_at = ?,
                    last_collection_status = ?,
                    last_collection_error = ?,
                    last_collection_count = ?,
                    last_collection_run_id = ?
                WHERE id = ?
                """,
                (
                    finished_at,
                    account_status,
                    error,
                    submittable_count,
                    run_id,
                    row["account_id"],
                ),
            )
        return self.get_collection_run(run_id)

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
        parent_job_id: str | None = None,
        attempt: int = 1,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        created_at = utc_now()
        try:
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO jobs(
                      id, account_id, assignment_id, parent_job_id, attempt,
                      status, submission_status, auto_submit, interactive,
                      created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'queued', 'not_started', ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        account_id,
                        assignment_id,
                        parent_job_id,
                        attempt,
                        int(auto_submit),
                        int(interactive),
                        created_at,
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as error:
            if self.has_active_job_for_assignment(assignment_id):
                raise ActiveJobConflict(
                    "该作业已有排队或运行中的任务"
                ) from error
            raise
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT jobs.*, assignments.title AS assignment_title,
                  assignments.course_name, assignments.task_url,
                  accounts.name AS account_name
                FROM jobs
                JOIN assignments ON assignments.id = jobs.assignment_id
                JOIN accounts ON accounts.id = jobs.account_id
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
                  assignments.course_name, assignments.task_url,
                  accounts.name AS account_name
                FROM jobs
                JOIN assignments ON assignments.id = jobs.assignment_id
                JOIN accounts ON accounts.id = jobs.account_id
                ORDER BY jobs.created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def next_attempt_for_assignment(
        self,
        assignment_id: str,
    ) -> tuple[str | None, int]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, attempt
                FROM jobs
                WHERE assignment_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (assignment_id,),
            ).fetchone()
            max_row = connection.execute(
                "SELECT COALESCE(MAX(attempt), 0) AS attempt FROM jobs "
                "WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
        if not row:
            return None, 1
        return row["id"], int(max_row["attempt"] or 0) + 1

    def stale_running_jobs(
        self,
        threshold_seconds: int = 120,
    ) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=max(30, int(threshold_seconds))
        )
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT jobs.*, assignments.title AS assignment_title,
                  accounts.name AS account_name
                FROM jobs
                JOIN assignments ON assignments.id = jobs.assignment_id
                JOIN accounts ON accounts.id = jobs.account_id
                WHERE jobs.status = 'running'
                ORDER BY jobs.created_at
                """
            ).fetchall()
            stale = []
            for row in rows:
                basis_value = (
                    row["last_heartbeat_at"]
                    or row["started_at"]
                    or row["updated_at"]
                    or row["created_at"]
                )
                try:
                    basis = datetime.fromisoformat(basis_value)
                    if basis.tzinfo is None:
                        basis = basis.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    basis = cutoff
                if basis > cutoff:
                    continue
                already_reported = connection.execute(
                    """
                    SELECT 1 FROM job_events
                    WHERE job_id = ? AND kind = 'stalled'
                      AND created_at > ?
                    LIMIT 1
                    """,
                    (row["id"], basis.isoformat()),
                ).fetchone()
                if not already_reported:
                    stale.append(dict(row))
        return stale

    def update_submission_status(
        self,
        job_id: str,
        status: str,
        note: str = "",
    ) -> dict[str, Any]:
        allowed = {
            "submitted_verified",
            "filled_verified",
            "not_submitted",
            "submission_unknown",
            "filled_pending_review",
            "partial_pending_review",
        }
        if status not in allowed:
            raise ValueError("不支持的提交结果")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                raise KeyError("任务不存在")
            if row["status"] in ACTIVE_JOB_STATUSES:
                raise RuntimeError("任务仍在执行，不能人工确认结果")
            connection.execute(
                """
                UPDATE jobs
                SET submission_status = ?, verification_note = ?,
                    verified_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, note, utc_now(), utc_now(), job_id),
            )
        return self.get_job(job_id)

    def has_active_job(self, account_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM jobs
                WHERE account_id = ?
                  AND status IN (
                    'queued',
                    'running',
                    'paused',
                    'stopping',
                    'needs_intervention'
                  )
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
        return row is not None

    def has_active_job_for_assignment(self, assignment_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM jobs
                WHERE assignment_id = ?
                  AND status IN (
                    'queued',
                    'running',
                    'paused',
                    'stopping',
                    'needs_intervention'
                  )
                LIMIT 1
                """,
                (assignment_id,),
            ).fetchone()
        return row is not None

    def mark_interrupted_jobs(self) -> None:
        finished_at = utc_now()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, status FROM jobs
                WHERE status IN (
                  'queued',
                  'running',
                  'paused',
                  'stopping',
                  'needs_intervention'
                )
                """
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'interrupted',
                        submission_status = CASE
                          WHEN submission_status = 'not_started' THEN 'not_submitted'
                          ELSE submission_status
                        END,
                        error = '任务已中断：应用或本地后端重新启动，原浏览器会话无法继续。建议重新执行任务；若原任务已有填写内容，可先进入作业页人工核对。',
                        finished_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (finished_at, finished_at, row["id"]),
                )
                connection.execute(
                    """
                    INSERT INTO job_events(job_id, kind, message, data_json, created_at)
                    VALUES (?, 'interrupted', ?, ?, ?)
                    """,
                    (
                        row["id"],
                        "任务已中断：应用重启导致浏览器会话结束",
                        json.dumps(
                            {"previous_status": row["status"]},
                            ensure_ascii=False,
                        ),
                        finished_at,
                    ),
                )

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields.setdefault("updated_at", utc_now())
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = [*fields.values(), job_id]
        with self.connect() as connection:
            connection.execute(f"UPDATE jobs SET {columns} WHERE id = ?", values)

    def transition_job(self, job_id: str, status: str, **fields: Any) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                raise KeyError("任务不存在")
            current = row["status"]
            if status != current and status not in JOB_TRANSITIONS.get(current, set()):
                raise ValueError(f"任务状态不能从 {current} 转换为 {status}")
            fields["status"] = status
            fields.setdefault("updated_at", utc_now())
            columns = ", ".join(f"{key} = ?" for key in fields)
            values = [*fields.values(), job_id]
            connection.execute(
                f"UPDATE jobs SET {columns} WHERE id = ?",
                values,
            )

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
