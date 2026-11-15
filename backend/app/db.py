from __future__ import annotations

import json
import random
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import Settings
from .diagnostics import DiagnosticStore
from .preferences import load_preferences


ACTIVE_JOB_STATUSES = {
    "queued",
    "running",
    "paused",
    "stopping",
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
    "incomplete": {"running"},
    "failed": set(),
    "needs_intervention": {"running", "stopped", "failed", "interrupted", "completed", "incomplete"},
    "interrupted": set(),
}


class ActiveJobConflict(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def assignment_record(row) -> dict:
    result = dict(row)
    try:
        assessment = json.loads(result.pop("assessment_json", "{}") or "{}")
    except (ValueError, TypeError):
        assessment = {}
    result["assessment"] = assessment if isinstance(assessment, dict) else {}
    return result


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.diagnostics = DiagnosticStore(settings)
        self.diagnostics.retention_provider = lambda: load_preferences(self).system
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
                  queue_priority INTEGER NOT NULL DEFAULT 0,
                  waiting_reason TEXT,
                  not_before TEXT,
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

                CREATE TABLE IF NOT EXISTS model_calls (
                  id TEXT PRIMARY KEY,
                  attempt_id TEXT NOT NULL,
                  role TEXT NOT NULL,
                  provider_id TEXT NOT NULL,
                  model TEXT NOT NULL,
                  request_hash TEXT NOT NULL,
                  usage_json TEXT,
                  status TEXT NOT NULL,
                  latency_ms INTEGER,
                  failure_class TEXT,
                  http_status INTEGER,
                  candidate_index INTEGER NOT NULL DEFAULT 0,
                  fallback_from TEXT,
                  idempotency_key TEXT,
                  estimated_tokens INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS task_checkpoints (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  attempt_id TEXT NOT NULL,
                  attempt_kind TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  phase TEXT NOT NULL,
                  goal_json TEXT NOT NULL DEFAULT '[]',
                  completed_json TEXT NOT NULL DEFAULT '[]',
                  blockers_json TEXT NOT NULL DEFAULT '[]',
                  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                  next_action_id TEXT,
                  created_at TEXT NOT NULL,
                  UNIQUE(attempt_id, version)
                );

                CREATE TABLE IF NOT EXISTS resource_leases (
                  resource_key TEXT PRIMARY KEY,
                  owner_id TEXT NOT NULL,
                  owner_kind TEXT NOT NULL,
                  acquired_at TEXT NOT NULL,
                  heartbeat_at TEXT NOT NULL,
                  expires_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_provider_health (
                  provider_id TEXT PRIMARY KEY,
                  state TEXT NOT NULL DEFAULT 'closed',
                  consecutive_failures INTEGER NOT NULL DEFAULT 0,
                  opened_until REAL,
                  last_failure_class TEXT,
                  last_failure_at TEXT,
                  last_success_at TEXT,
                  adaptive_concurrency INTEGER NOT NULL DEFAULT 2,
                  cooldown_seconds REAL NOT NULL DEFAULT 30,
                  probe_started_at REAL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_sessions (
                  id TEXT PRIMARY KEY,
                  agent_kind TEXT NOT NULL,
                  channel TEXT NOT NULL,
                  external_user_ref TEXT,
                  status TEXT NOT NULL DEFAULT 'active',
                  title TEXT,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  deleted_at TEXT
                );

                CREATE TABLE IF NOT EXISTS agent_messages (
                  id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
                  sequence INTEGER NOT NULL,
                  role TEXT NOT NULL,
                  content_json TEXT NOT NULL,
                  model_call_id TEXT,
                  kind TEXT NOT NULL DEFAULT 'message',
                  sensitive INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  UNIQUE(session_id, sequence)
                );

                CREATE TABLE IF NOT EXISTS agent_summaries (
                  id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
                  from_sequence INTEGER NOT NULL,
                  to_sequence INTEGER NOT NULL,
                  summary TEXT NOT NULL,
                  model_call_id TEXT,
                  created_at TEXT NOT NULL,
                  UNIQUE(session_id, to_sequence)
                );

                CREATE TABLE IF NOT EXISTS memory_items (
                  id TEXT PRIMARY KEY,
                  scope TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  content TEXT NOT NULL,
                  source_ref TEXT NOT NULL,
                  source_kind TEXT NOT NULL,
                  confidence REAL NOT NULL DEFAULT 1,
                  sensitive INTEGER NOT NULL DEFAULT 0,
                  expires_at TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  deleted_at TEXT
                );

                CREATE TABLE IF NOT EXISTS memory_tombstones (
                  id TEXT PRIMARY KEY,
                  target_kind TEXT NOT NULL,
                  target_id TEXT NOT NULL,
                  scope TEXT,
                  reason TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS channel_messages (
                  id TEXT PRIMARY KEY,
                  channel TEXT NOT NULL,
                  external_message_id TEXT NOT NULL,
                  session_id TEXT,
                  sender_id TEXT NOT NULL,
                  message_type TEXT NOT NULL,
                  content_json TEXT NOT NULL,
                  processed_at TEXT,
                  created_at TEXT NOT NULL,
                  UNIQUE(channel, external_message_id)
                );

                CREATE TABLE IF NOT EXISTS channel_users (
                  channel TEXT NOT NULL,
                  external_user_id TEXT NOT NULL,
                  role TEXT NOT NULL DEFAULT 'viewer',
                  account_scope_json TEXT NOT NULL DEFAULT '[]',
                  readonly_account_ids_json TEXT NOT NULL DEFAULT '[]',
                  enabled INTEGER NOT NULL DEFAULT 0,
                  display_name TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(channel, external_user_id)
                );

                CREATE TABLE IF NOT EXISTS channel_contexts (
                  channel TEXT NOT NULL,
                  external_user_id TEXT NOT NULL,
                  context_token TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(channel, external_user_id)
                );

                CREATE TABLE IF NOT EXISTS pending_actions (
                  id TEXT PRIMARY KEY,
                  channel TEXT NOT NULL,
                  external_user_id TEXT NOT NULL,
                  session_id TEXT NOT NULL,
                  inbound_message_id TEXT NOT NULL,
                  action_kind TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  action_hash TEXT NOT NULL,
                  token_hash TEXT NOT NULL UNIQUE,
                  expires_at TEXT NOT NULL,
                  state TEXT NOT NULL DEFAULT 'pending',
                  idempotency_key TEXT NOT NULL UNIQUE,
                  created_at TEXT NOT NULL,
                  consumed_at TEXT,
                  cancelled_at TEXT
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                  id TEXT PRIMARY KEY,
                  attempt_id TEXT,
                  channel TEXT,
                  external_user_id TEXT,
                  tool TEXT NOT NULL,
                  arguments_json TEXT NOT NULL DEFAULT '{}',
                  receipt_json TEXT NOT NULL DEFAULT '{}',
                  idempotency_key TEXT,
                  status TEXT NOT NULL,
                  error_code TEXT,
                  created_at TEXT NOT NULL,
                  finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS incidents (
                  id TEXT PRIMARY KEY,
                  dedupe_key TEXT NOT NULL UNIQUE,
                  severity TEXT NOT NULL,
                  status TEXT NOT NULL,
                  title TEXT NOT NULL,
                  details TEXT NOT NULL,
                  source_ref TEXT,
                  next_action TEXT,
                  opened_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL,
                  resolved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS outbox_messages (
                  id TEXT PRIMARY KEY,
                  channel TEXT NOT NULL,
                  external_user_id TEXT NOT NULL,
                  dedupe_key TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  state TEXT NOT NULL DEFAULT 'pending',
                  attempts INTEGER NOT NULL DEFAULT 0,
                  next_attempt_at TEXT NOT NULL,
                  last_error TEXT,
                  created_at TEXT NOT NULL,
                  sent_at TEXT,
                  UNIQUE(channel, dedupe_key)
                );

                CREATE INDEX IF NOT EXISTS idx_assignments_account
                  ON assignments(account_id, academic_year, status);
                CREATE INDEX IF NOT EXISTS idx_job_events_job
                  ON job_events(job_id, id);
                CREATE INDEX IF NOT EXISTS idx_collection_runs_account
                  ON collection_runs(account_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_model_calls_attempt
                  ON model_calls(attempt_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_model_calls_health
                  ON model_calls(provider_id, model, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_task_checkpoints_attempt
                  ON task_checkpoints(attempt_id, version);
                CREATE INDEX IF NOT EXISTS idx_resource_leases_expiry
                  ON resource_leases(expires_at);
                CREATE INDEX IF NOT EXISTS idx_resource_leases_owner
                  ON resource_leases(owner_id);
                CREATE INDEX IF NOT EXISTS idx_agent_messages_session
                  ON agent_messages(session_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_agent_summaries_session
                  ON agent_summaries(session_id, to_sequence);
                CREATE INDEX IF NOT EXISTS idx_memory_items_scope
                  ON memory_items(scope, deleted_at, expires_at, updated_at);
                CREATE INDEX IF NOT EXISTS idx_memory_tombstones_target
                  ON memory_tombstones(target_kind, target_id);
                CREATE INDEX IF NOT EXISTS idx_channel_messages_sender
                  ON channel_messages(channel, sender_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_pending_actions_actor
                  ON pending_actions(channel, external_user_id, state, expires_at);
                CREATE INDEX IF NOT EXISTS idx_tool_calls_attempt
                  ON tool_calls(attempt_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_incidents_status
                  ON incidents(status, severity, last_seen_at);
                CREATE INDEX IF NOT EXISTS idx_outbox_dispatch
                  ON outbox_messages(state, next_attempt_at);
                DROP INDEX IF EXISTS idx_jobs_active_assignment;
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
            if "browser_retained" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN browser_retained INTEGER NOT NULL DEFAULT 0"
                )
            for column, definition in {
                "phase": "TEXT NOT NULL DEFAULT 'queued'",
                "completed_units": "INTEGER NOT NULL DEFAULT 0",
                "total_units": "INTEGER NOT NULL DEFAULT 0",
                "deleted_at": "TEXT",
                "queue_priority": "INTEGER NOT NULL DEFAULT 0",
                "waiting_reason": "TEXT",
                "not_before": "TEXT",
            }.items():
                if column not in job_columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
            model_call_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(model_calls)").fetchall()
            }
            if "estimated_tokens" not in model_call_columns:
                connection.execute(
                    "ALTER TABLE model_calls ADD COLUMN estimated_tokens INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                CREATE UNIQUE INDEX idx_jobs_active_assignment ON jobs(assignment_id)
                WHERE status IN ('queued', 'running', 'paused', 'stopping')
                  OR browser_retained = 1
                """
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
            connection.execute("UPDATE jobs SET status = 'interrupted' WHERE status = 'failed'")
            connection.execute(
                """
                UPDATE jobs SET status = 'needs_intervention',
                  error = '填写内容已审核，尚未确认提交。'
                WHERE submission_status = 'filled_verified'
                  AND status NOT IN ('queued', 'running', 'paused', 'stopping')
                """
            )
            connection.execute(
                """
                UPDATE jobs SET status = CASE
                  WHEN submission_status IN ('filled_pending_review', 'filled_verified')
                    THEN 'needs_intervention'
                  ELSE 'incomplete'
                END
                WHERE status = 'completed' AND submission_status != 'submitted_verified'
                """
            )
            connection.execute(
                """
                UPDATE jobs SET status = 'incomplete'
                WHERE status = 'needs_intervention'
                  AND submission_status IN ('partial_pending_review', 'not_submitted', 'submission_unknown')
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
            for name, definition in {
                "auto_collect_interval_seconds": "INTEGER NOT NULL DEFAULT 21600",
                "auto_collect_interval_value": "REAL NOT NULL DEFAULT 6",
                "auto_collect_interval_unit": "TEXT NOT NULL DEFAULT 'h'",
                "auto_execute_enabled": "INTEGER NOT NULL DEFAULT 0",
                "automation_baseline_ready": "INTEGER NOT NULL DEFAULT 0",
                "platform_user_id": "TEXT",
                "avatar_url": "TEXT",
                "avatar_mime": "TEXT",
                "identity_verified_at": "TEXT",
            }.items():
                if name not in account_columns:
                    connection.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")
            if "auto_collect_interval_seconds" not in account_columns:
                connection.execute("UPDATE accounts SET auto_collect_interval_seconds = auto_collect_interval_minutes * 60, "
                                   "auto_collect_interval_value = auto_collect_interval_minutes, auto_collect_interval_unit = 'min'")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_account_platform_identity ON accounts(platform,platform_user_id) WHERE platform_user_id IS NOT NULL AND platform_user_id != ''")
            connection.execute("""CREATE TABLE IF NOT EXISTS automatic_assignments (
                assignment_id TEXT PRIMARY KEY REFERENCES assignments(id) ON DELETE CASCADE,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', job_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS assignment_auto_groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                account_ids_json TEXT NOT NULL,
                course_filters_json TEXT NOT NULL,
                windows_json TEXT NOT NULL,
                auto_submit INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                baseline_ready INTEGER NOT NULL DEFAULT 0,
                last_scan_at TEXT,
                last_status TEXT NOT NULL DEFAULT 'waiting',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS assignment_auto_group_items (
                group_id TEXT NOT NULL REFERENCES assignment_auto_groups(id) ON DELETE CASCADE,
                assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                state TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                job_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(group_id, assignment_id)
            )""")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS assignment_auto_group_items_state "
                "ON assignment_auto_group_items(state,created_at)"
            )
            job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "trigger" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual'")
            connection.execute(
                """
                UPDATE jobs SET queue_priority = CASE
                  WHEN trigger = 'manual' THEN 100
                  ELSE 10
                END
                WHERE queue_priority = 0
                """
            )
            if "entry_url" not in account_columns:
                connection.execute("ALTER TABLE accounts ADD COLUMN entry_url TEXT")
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
            if "assessment_json" not in assignment_columns:
                connection.execute("ALTER TABLE assignments ADD COLUMN assessment_json TEXT NOT NULL DEFAULT '{}'")
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

    @staticmethod
    def _account_has_history(connection, account_id: str) -> bool:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        for table in tables:
            quoted = '"' + table["name"].replace('"', '""') + '"'
            for foreign_key in connection.execute(f"PRAGMA foreign_key_list({quoted})"):
                if foreign_key["table"] != "accounts":
                    continue
                column = '"' + foreign_key["from"].replace('"', '""') + '"'
                if connection.execute(f"SELECT 1 FROM {quoted} WHERE {column} = ? LIMIT 1", (account_id,)).fetchone():
                    return True
        return False

    def account_has_history(self, account_id: str) -> bool:
        with self.connect() as connection:
            return self._account_has_history(connection, account_id)

    def discard_pending_account(self, account_id: str) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if not account or account["platform_user_id"] or account["identity_verified_at"] or self._account_has_history(connection, account_id):
                raise RuntimeError("该记录已有身份或业务数据，不能作为临时授权记录移除。")
            connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def delete_account(self, account_id: str) -> None:
        with self.connect() as connection:
            owner_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM jobs WHERE account_id = ?",
                    (account_id,),
                ).fetchall()
            ]
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_runs'"
            ).fetchone():
                owner_ids.extend(
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM learning_runs WHERE account_id = ?",
                        (account_id,),
                    ).fetchall()
                )
            if owner_ids:
                connection.executemany(
                    "DELETE FROM resource_leases WHERE owner_id = ?",
                    ((owner_id,) for owner_id in owner_ids),
                )
            self.forget_attempt_records(
                connection,
                owner_ids,
                reason="account_deleted",
                scopes=[
                    f"account:{account_id}",
                    *(f"attempt:{owner_id}" for owner_id in owner_ids),
                ],
            )
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
        from .services.assignments.availability import assignment_expired
        assignment = {**assignment, "submittable": bool(assignment.get("submittable")) and not assignment_expired(assignment)}
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
            if "assessment" in assignment:
                connection.execute("UPDATE assignments SET assessment_json = ? WHERE id = ?",
                    (json.dumps(assignment["assessment"], ensure_ascii=False), assignment_id))
        return assignment_id

    def update_assignment_assessment(self, assignment_id: str, assessment: dict) -> None:
        with self.connect() as connection:
            changed = connection.execute("UPDATE assignments SET assessment_json = ? WHERE id = ?",
                (json.dumps(assessment, ensure_ascii=False), assignment_id)).rowcount
            if not changed: raise KeyError("作业不存在")

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
        from .services.assignments.availability import assignment_expired
        records = [assignment_record(row) for row in rows]
        return [item for item in records if not assignment_expired(item)] if submittable_only else records

    def finalize_assignment_snapshot(
        self,
        account_id: str,
        academic_year: int,
        run_id: str,
        failed_course_ids: set[str] | None = None,
        course_ids: list[str] | None = None,
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
        if course_ids is not None:
            if not course_ids: return
            clauses.append("course_id IN (" + ",".join("?" for _ in course_ids) + ")")
            values.extend(course_ids)
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
        return assignment_record(row)

    def create_job(
        self,
        account_id: str,
        assignment_id: str,
        auto_submit: bool,
        interactive: bool = True,
        parent_job_id: str | None = None,
        attempt: int = 1,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        created_at = utc_now()
        queue_priority = 100 if trigger == "manual" else 10
        try:
            with self.connect() as connection:
                if trigger == "scheduled":
                    changed = connection.execute(
                        "UPDATE automatic_assignments SET state = 'queued', job_id = ?, updated_at = ? "
                        "WHERE assignment_id = ? AND state = 'pending'",
                        (job_id, created_at, assignment_id),
                    ).rowcount
                    if not changed:
                        raise ActiveJobConflict("自动任务已入队或已取消")
                connection.execute(
                    """
                    INSERT INTO jobs(
                      id, account_id, assignment_id, parent_job_id, attempt,
                      status, submission_status, auto_submit, interactive,
                      created_at, updated_at, trigger, queue_priority
                    ) VALUES (?, ?, ?, ?, ?, 'queued', 'not_started', ?, ?, ?, ?, ?, ?)
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
                        trigger,
                        queue_priority,
                    ),
                )
        except sqlite3.IntegrityError as error:
            if self.has_active_job_for_assignment(assignment_id):
                raise ActiveJobConflict(
                    "该作业已有排队或运行中的任务"
                ) from error
            raise
        job = self.get_job(job_id)
        self.diagnostics.start("assignment", job_id, job, self.get_account(account_id))
        return job

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT jobs.*, assignments.title AS assignment_title,
                  assignments.course_name, assignments.task_url, assignments.platform,
                  accounts.name AS account_name, accounts.status AS account_status,
                  assignments.submittable AS assignment_submittable
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

    def list_jobs(self, *, include_deleted: bool = True) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT jobs.*, assignments.title AS assignment_title,
                  assignments.course_name, assignments.task_url, assignments.platform,
                  accounts.name AS account_name, accounts.status AS account_status,
                  assignments.submittable AS assignment_submittable
                FROM jobs
                JOIN assignments ON assignments.id = jobs.assignment_id
                JOIN accounts ON accounts.id = jobs.account_id
                WHERE (? OR jobs.deleted_at IS NULL)
                ORDER BY julianday(jobs.created_at) DESC, jobs.rowid DESC
                """,
                (include_deleted,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_job_task(self, job_id: str) -> dict[str, Any]:
        # Keep evidence and submission guards while removing every displayed attempt.
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute("SELECT account_id, assignment_id FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise KeyError("任务不存在")
            attempts = connection.execute(
                "SELECT id,status,browser_retained FROM jobs WHERE account_id=? AND assignment_id=?",
                (job["account_id"], job["assignment_id"]),
            ).fetchall()
            if any(row["status"] in ACTIVE_JOB_STATUSES or row["browser_retained"] for row in attempts):
                raise ActiveJobConflict("请先停止任务并关闭任务浏览器，再删除任务。")
            connection.execute(
                "UPDATE jobs SET deleted_at=? WHERE account_id=? AND assignment_id=? AND deleted_at IS NULL",
                (utc_now(), job["account_id"], job["assignment_id"]),
            )
            self.forget_attempt_records(
                connection,
                [row["id"] for row in attempts],
                reason="assignment_task_deleted",
                scopes=[f"attempt:{row['id']}" for row in attempts],
            )
            for row in attempts:
                connection.execute(
                    "DELETE FROM resource_leases WHERE owner_id = ?",
                    (row["id"],),
                )
        return {"deleted_ids": [row["id"] for row in attempts], "task_kind": "assignment"}

    def forget_attempt_records(
        self,
        connection: sqlite3.Connection,
        attempt_ids: list[str],
        *,
        reason: str,
        scopes: list[str] | None = None,
    ) -> dict[str, int]:
        identifiers = list(dict.fromkeys(str(item) for item in attempt_ids if item))
        scope_values = list(dict.fromkeys(str(item) for item in (scopes or []) if item))
        if not identifiers and not scope_values:
            return {"checkpoints": 0, "memories": 0}
        now = utc_now()
        memory_clauses: list[str] = []
        memory_values: list[Any] = []
        if identifiers:
            source_refs = []
            for identifier in identifiers:
                source_refs.extend([
                    identifier,
                    f"task:{identifier}",
                    f"assignment:{identifier}",
                    f"learning:{identifier}",
                ])
            placeholders = ",".join("?" for _ in source_refs)
            memory_clauses.append(f"source_ref IN ({placeholders})")
            memory_values.extend(source_refs)
        if scope_values:
            placeholders = ",".join("?" for _ in scope_values)
            memory_clauses.append(f"scope IN ({placeholders})")
            memory_values.extend(scope_values)
        memory_rows = connection.execute(
            f"""
            SELECT id, scope FROM memory_items
            WHERE deleted_at IS NULL AND ({' OR '.join(memory_clauses)})
            """,
            memory_values,
        ).fetchall()
        for row in memory_rows:
            connection.execute(
                "UPDATE memory_items SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )
            connection.execute(
                """
                INSERT INTO memory_tombstones(
                  id, target_kind, target_id, scope, reason, created_at
                ) VALUES (?, 'memory_item', ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, row["id"], row["scope"], reason, now),
            )
        checkpoint_count = 0
        if identifiers:
            placeholders = ",".join("?" for _ in identifiers)
            cursor = connection.execute(
                f"DELETE FROM task_checkpoints WHERE attempt_id IN ({placeholders})",
                identifiers,
            )
            checkpoint_count = int(cursor.rowcount)
            for identifier in identifiers:
                connection.execute(
                    """
                    INSERT INTO memory_tombstones(
                      id, target_kind, target_id, scope, reason, created_at
                    ) VALUES (?, 'task_attempt', ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        identifier,
                        f"attempt:{identifier}",
                        reason,
                        now,
                    ),
                )
        return {
            "checkpoints": checkpoint_count,
            "memories": len(memory_rows),
        }

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
                ORDER BY attempt DESC, julianday(created_at) DESC
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
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, assignment_id FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                raise KeyError("任务不存在")
            if row["status"] in ACTIVE_JOB_STATUSES:
                raise RuntimeError("任务仍在执行，不能人工确认结果")
            if row["status"] == "completed" and status != "submitted_verified":
                raise RuntimeError("该任务已确认提交，不能修改为未提交或重新填写")
            other = connection.execute(
                """SELECT id FROM jobs WHERE assignment_id = ? AND id != ?
                   AND (status IN ('queued','running','paused','stopping') OR browser_retained = 1)
                   LIMIT 1""", (row["assignment_id"], job_id),
            ).fetchone()
            if other:
                raise RuntimeError("该作业已有新任务，请先处理当前任务后再核对历史结果")
            job_status = {
                "submitted_verified": "completed",
                "filled_verified": "needs_intervention",
                "filled_pending_review": "needs_intervention",
                "partial_pending_review": "incomplete",
                "not_submitted": "incomplete",
                "submission_unknown": "interrupted",
            }[status]
            connection.execute(
                """
                UPDATE jobs
                SET submission_status = ?, verification_note = ?, status = ?,
                    verified_at = ?, updated_at = ?,
                    progress = CASE WHEN ? = 'completed' THEN 100 ELSE progress END,
                    error = CASE
                      WHEN ? = 'completed' THEN NULL
                      WHEN ? = 'needs_intervention' THEN '已填写并等待人工审核，尚未确认提交。'
                      ELSE error END
                    , finished_at = CASE WHEN ? = 'completed' THEN COALESCE(finished_at, ?) ELSE finished_at END
                    , phase = CASE WHEN ? = 'completed' THEN 'complete' WHEN ? = 'needs_intervention' THEN 'review' ELSE ? END
                WHERE id = ?
                """,
                (status, note, job_status, utc_now(), utc_now(), job_status, job_status, job_status,
                 job_status, utc_now(), job_status, job_status, job_status, job_id),
            )
        return self.get_job(job_id)

    def has_active_job(self, account_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM jobs
                WHERE account_id = ?
                  AND (status IN (
                    'queued',
                    'running',
                    'paused',
                    'stopping'
                  ) OR browser_retained = 1)
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
                  AND (status IN (
                    'queued',
                    'running',
                    'paused',
                    'stopping'
                  ) OR browser_retained = 1)
                LIMIT 1
                """,
                (assignment_id,),
            ).fetchone()
        return row is not None

    def mark_interrupted_jobs(self) -> None:
        finished_at = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET browser_retained = 0,
                  finished_at = COALESCE(finished_at, ?)
                WHERE status IN ('needs_intervention', 'incomplete')
                """,
                (finished_at,),
            )
            rows = connection.execute(
                """
                SELECT id, status FROM jobs
                WHERE status IN ('running', 'paused', 'stopping')
                """
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'interrupted',
                        phase = 'interrupted',
                        browser_retained = 0,
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

        for row in rows:
            trace = self.diagnostics.trace("assignment", row["id"])
            if trace.available:
                trace.record("task.recovered", "Service restarted; previous browser session ended", {"previous_status": row["status"]}, state="interrupted", phase="interrupted")
                trace.summary(self.get_job(row["id"]))
        for job in self.list_jobs():
            trace = self.diagnostics.trace("assignment", job["id"])
            if trace.available and trace.manifest.get("browser_retained") and not job.get("browser_retained"): trace.summary(job)

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        if "status" in fields:
            return self.transition_job(job_id, fields.pop("status"), **fields)
        fields.setdefault("updated_at", utc_now())
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = [*fields.values(), job_id]
        with self.connect() as connection:
            connection.execute(f"UPDATE jobs SET {columns} WHERE id = ?", values)
        if "browser_retained" in fields:
            trace = self.diagnostics.trace("assignment", job_id)
            if trace.available: trace.summary(self.get_job(job_id))

    def transition_job(self, job_id: str, status: str, **fields: Any) -> None:
        trace = self.diagnostics.trace("assignment", job_id)
        if trace.available and fields.get("error"): fields["error"] = trace.scrubber.text(fields["error"])
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, submission_status FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                raise KeyError("任务不存在")
            current = row["status"]
            if status == "completed" and fields.get("submission_status", row["submission_status"]) != "submitted_verified":
                raise ValueError("只有确认作业完成且提交成功后，才能标记已完成")
            if status == "needs_intervention" and fields.get("submission_status", row["submission_status"]) not in {"filled_pending_review", "filled_verified"}:
                raise ValueError("只有全部填写完成后，才能进入待介入审核")
            if status != current and status not in JOB_TRANSITIONS.get(current, set()):
                raise ValueError(f"任务状态不能从 {current} 转换为 {status}")
            fields["status"] = status
            fields.setdefault("updated_at", utc_now())
            if status in {
                "completed",
                "incomplete",
                "needs_intervention",
                "failed",
                "interrupted",
                "stopped",
            }:
                fields.setdefault("waiting_reason", None)
                fields.setdefault("not_before", None)
            columns = ", ".join(f"{key} = ?" for key in fields)
            values = [*fields.values(), job_id]
            connection.execute(
                f"UPDATE jobs SET {columns} WHERE id = ?",
                values,
            )
        trace = self.diagnostics.trace("assignment", job_id)
        if trace.available:
            if status != current: trace.record("task.transition", data={"from": current, "to": status}, state=status, phase=fields.get("phase"))
            trace.summary(self.get_job(job_id))

    def add_event(
        self,
        job_id: str,
        kind: str,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace = self.diagnostics.trace("assignment", job_id)
        if trace.available:
            message, data = trace.prepare_event(message, data)
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
        trace = self.diagnostics.trace("assignment", job_id)
        if trace.available:
            task = self.get_job(job_id)
            trace.record("assignment." + kind, message, data, state=task["status"], phase=task.get("phase"), source_event_id=event_id)
            trace.summary(task)
        return {
            "id": event_id,
            "job_id": job_id,
            "kind": kind,
            "message": message,
            "data": data or {},
            "created_at": created_at,
        }

    def list_events(self, job_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id = ? AND id > ? ORDER BY id",
                (job_id, after_id),
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

    def begin_model_call(
        self,
        *,
        attempt_id: str,
        role: str,
        provider_id: str,
        model: str,
        request_hash: str,
        candidate_index: int = 0,
        fallback_from: str | None = None,
        idempotency_key: str | None = None,
        estimated_tokens: int = 0,
    ) -> dict[str, Any]:
        call_id = uuid.uuid4().hex
        created_at = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_calls(
                  id, attempt_id, role, provider_id, model, request_hash,
                  status, candidate_index, fallback_from, idempotency_key,
                  estimated_tokens, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    attempt_id,
                    role,
                    provider_id,
                    model,
                    request_hash,
                    candidate_index,
                    fallback_from,
                    idempotency_key,
                    max(0, int(estimated_tokens)),
                    created_at,
                ),
            )
        return {
            "id": call_id,
            "attempt_id": attempt_id,
            "role": role,
            "provider_id": provider_id,
            "model": model,
            "request_hash": request_hash,
            "status": "running",
            "candidate_index": candidate_index,
            "fallback_from": fallback_from,
            "idempotency_key": idempotency_key,
            "estimated_tokens": max(0, int(estimated_tokens)),
            "created_at": created_at,
        }

    def finish_model_call(
        self,
        call_id: str,
        *,
        status: str,
        latency_ms: int,
        usage: Any = None,
        failure_class: str | None = None,
        http_status: int | None = None,
    ) -> None:
        if status not in {"succeeded", "error", "cancelled"}:
            raise ValueError("未知模型调用状态")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE model_calls
                SET status = ?, latency_ms = ?, usage_json = ?,
                    failure_class = ?, http_status = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    max(0, int(latency_ms)),
                    json.dumps(usage, ensure_ascii=False) if usage is not None else None,
                    failure_class,
                    http_status,
                    utc_now(),
                    call_id,
                ),
            )

    def list_model_calls(
        self,
        *,
        attempt_id: str | None = None,
        provider_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if attempt_id:
            clauses.append("attempt_id = ?")
            values.append(attempt_id)
        if provider_id:
            clauses.append("provider_id = ?")
            values.append(provider_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 1000)))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM model_calls
                {where}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["usage"] = json.loads(item.pop("usage_json", "") or "null")
            except (TypeError, ValueError):
                item["usage"] = None
            result.append(item)
        return result

    def save_task_checkpoint(
        self,
        *,
        attempt_id: str,
        attempt_kind: str,
        phase: str,
        goal: Any = None,
        completed: Any = None,
        blockers: Any = None,
        evidence_refs: Any = None,
        next_action_id: str | None = None,
    ) -> dict[str, Any]:
        created_at = utc_now()
        goal_json = json.dumps(goal if goal is not None else [], ensure_ascii=False)
        completed_json = json.dumps(completed if completed is not None else [], ensure_ascii=False)
        blockers_json = json.dumps(blockers if blockers is not None else [], ensure_ascii=False)
        evidence_json = json.dumps(evidence_refs if evidence_refs is not None else [], ensure_ascii=False)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) AS version
                FROM task_checkpoints WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            version = int(row["version"]) + 1
            cursor = connection.execute(
                """
                INSERT INTO task_checkpoints(
                  attempt_id, attempt_kind, version, phase, goal_json,
                  completed_json, blockers_json, evidence_refs_json,
                  next_action_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    attempt_kind,
                    version,
                    phase,
                    goal_json,
                    completed_json,
                    blockers_json,
                    evidence_json,
                    next_action_id,
                    created_at,
                ),
            )
            checkpoint_id = cursor.lastrowid
        return {
            "id": checkpoint_id,
            "attempt_id": attempt_id,
            "attempt_kind": attempt_kind,
            "version": version,
            "phase": phase,
            "goal": goal if goal is not None else [],
            "completed": completed if completed is not None else [],
            "blockers": blockers if blockers is not None else [],
            "evidence_refs": evidence_refs if evidence_refs is not None else [],
            "next_action_id": next_action_id,
            "created_at": created_at,
        }

    def latest_task_checkpoint(self, attempt_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM task_checkpoints
                WHERE attempt_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (attempt_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        for source, target in (
            ("goal_json", "goal"),
            ("completed_json", "completed"),
            ("blockers_json", "blockers"),
            ("evidence_refs_json", "evidence_refs"),
        ):
            try:
                item[target] = json.loads(item.pop(source, "") or "[]")
            except (TypeError, ValueError):
                item[target] = []
        return item

    def cleanup_expired_resource_leases(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else float(now)
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM resource_leases WHERE expires_at <= ?",
                (current,),
            )
        return int(cursor.rowcount)

    def acquire_resource_leases(
        self,
        *,
        owner_id: str,
        owner_kind: str,
        resource_keys: list[str],
        ttl_seconds: float = 60,
        now: float | None = None,
    ) -> tuple[bool, str | None]:
        keys = list(dict.fromkeys(str(key) for key in resource_keys if str(key)))
        if not keys:
            raise ValueError("至少需要一个资源租约键")
        current = time.time() if now is None else float(now)
        expires_at = current + max(5.0, float(ttl_seconds))
        heartbeat_at = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM resource_leases WHERE expires_at <= ?",
                (current,),
            )
            placeholders = ",".join("?" for _ in keys)
            conflicts = connection.execute(
                f"""
                SELECT resource_key FROM resource_leases
                WHERE resource_key IN ({placeholders}) AND owner_id <> ?
                ORDER BY resource_key
                """,
                (*keys, owner_id),
            ).fetchall()
            if conflicts:
                return False, str(conflicts[0]["resource_key"])
            for key in keys:
                connection.execute(
                    """
                    INSERT INTO resource_leases(
                      resource_key, owner_id, owner_kind, acquired_at,
                      heartbeat_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(resource_key) DO UPDATE SET
                      owner_kind = excluded.owner_kind,
                      heartbeat_at = excluded.heartbeat_at,
                      expires_at = excluded.expires_at
                    WHERE resource_leases.owner_id = excluded.owner_id
                    """,
                    (
                        key,
                        owner_id,
                        owner_kind,
                        heartbeat_at,
                        heartbeat_at,
                        expires_at,
                    ),
                )
        return True, None

    def renew_resource_leases(
        self,
        owner_id: str,
        *,
        ttl_seconds: float = 60,
        now: float | None = None,
    ) -> int:
        current = time.time() if now is None else float(now)
        expires_at = current + max(5.0, float(ttl_seconds))
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE resource_leases
                SET heartbeat_at = ?, expires_at = ?
                WHERE owner_id = ?
                """,
                (utc_now(), expires_at, owner_id),
            )
        return int(cursor.rowcount)

    def release_resource_leases(
        self,
        owner_id: str,
        *,
        resource_keys: list[str] | None = None,
    ) -> int:
        with self.connect() as connection:
            if resource_keys:
                keys = list(dict.fromkeys(str(key) for key in resource_keys if str(key)))
                if not keys:
                    return 0
                placeholders = ",".join("?" for _ in keys)
                cursor = connection.execute(
                    f"DELETE FROM resource_leases WHERE owner_id = ? AND resource_key IN ({placeholders})",
                    (owner_id, *keys),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM resource_leases WHERE owner_id = ?",
                    (owner_id,),
                )
        return int(cursor.rowcount)

    def list_resource_leases(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM resource_leases ORDER BY resource_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def resource_lease(self, resource_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM resource_leases WHERE resource_key = ?",
                (resource_key,),
            ).fetchone()
        return dict(row) if row else None

    def get_model_provider_health(
        self,
        provider_id: str,
        *,
        default_concurrency: int = 2,
        cooldown_seconds: float = 30,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_provider_health(
                  provider_id, state, adaptive_concurrency, cooldown_seconds, updated_at
                ) VALUES (?, 'closed', ?, ?, ?)
                ON CONFLICT(provider_id) DO NOTHING
                """,
                (
                    provider_id,
                    max(1, int(default_concurrency)),
                    max(5.0, float(cooldown_seconds)),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM model_provider_health WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
        return dict(row)

    def claim_model_provider(
        self,
        provider_id: str,
        *,
        now: float | None = None,
        probe_ttl_seconds: float = 45,
    ) -> tuple[bool, str, float | None]:
        current = time.time() if now is None else float(now)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, opened_until, probe_started_at FROM model_provider_health WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            if not row:
                return True, "closed", None
            state = row["state"]
            opened_until = row["opened_until"]
            if state == "closed":
                return True, "closed", None
            if state == "half_open":
                probe_started = float(row["probe_started_at"] or 0)
                if probe_started + max(5.0, float(probe_ttl_seconds)) <= current:
                    connection.execute(
                        """
                        UPDATE model_provider_health
                        SET probe_started_at = ?, updated_at = ?
                        WHERE provider_id = ? AND state = 'half_open'
                        """,
                        (current, utc_now(), provider_id),
                    )
                    return True, "half_open", current + max(5.0, float(probe_ttl_seconds))
                return False, "half_open", opened_until
            if state == "open" and opened_until and float(opened_until) > current:
                return False, "open", float(opened_until)
            cursor = connection.execute(
                """
                UPDATE model_provider_health
                SET state = 'half_open', probe_started_at = ?, updated_at = ?
                WHERE provider_id = ? AND state = 'open'
                  AND (opened_until IS NULL OR opened_until <= ?)
                """,
                (current, utc_now(), provider_id, current),
            )
            if cursor.rowcount:
                return True, "half_open", current + max(5.0, float(probe_ttl_seconds))
            return False, "open", opened_until

    def record_model_provider_success(
        self,
        provider_id: str,
        *,
        max_concurrency: int,
        cooldown_seconds: float = 30,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT adaptive_concurrency FROM model_provider_health WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            adaptive = int(row["adaptive_concurrency"]) if row else max(1, int(max_concurrency))
            adaptive = min(max(1, int(max_concurrency)), adaptive + 1)
            connection.execute(
                """
                INSERT INTO model_provider_health(
                  provider_id, state, consecutive_failures, last_success_at,
                  adaptive_concurrency, cooldown_seconds, updated_at
                ) VALUES (?, 'closed', 0, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                  state = 'closed',
                  consecutive_failures = 0,
                  opened_until = NULL,
                  last_failure_class = NULL,
                  last_success_at = excluded.last_success_at,
                  adaptive_concurrency = ?,
                  cooldown_seconds = excluded.cooldown_seconds,
                  probe_started_at = NULL,
                  updated_at = excluded.updated_at
                """,
                (
                    provider_id,
                    utc_now(),
                    adaptive,
                    max(5.0, float(cooldown_seconds)),
                    utc_now(),
                    adaptive,
                ),
            )
            result = connection.execute(
                "SELECT * FROM model_provider_health WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
        return {**dict(result), "now": current}

    def record_model_provider_failure(
        self,
        provider_id: str,
        failure_class: str,
        *,
        threshold: int = 3,
        base_cooldown: float = 30,
        max_concurrency: int = 2,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        circuit_failures = {"auth", "rate_limit", "timeout", "network", "upstream_5xx"}
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM model_provider_health WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            previous_failures = int(row["consecutive_failures"]) if row else 0
            failures = (
                previous_failures + 1
                if failure_class in circuit_failures
                else previous_failures
            )
            adaptive = (
                int(row["adaptive_concurrency"])
                if row
                else max(1, int(max_concurrency))
            )
            previous_state = str(row["state"]) if row else "closed"
            cooldown = max(
                float(base_cooldown),
                float(row["cooldown_seconds"]) if row else 0,
            )
            if failure_class in {"rate_limit", "upstream_5xx"}:
                adaptive = max(1, adaptive // 2)
            should_open = (
                failure_class in circuit_failures
                and (failures >= max(1, int(threshold)) or previous_state == "half_open")
            )
            state = "open" if should_open else (previous_state if row else "closed")
            opened_until = float(row["opened_until"] or 0) if row else 0
            if should_open:
                cooldown = min(1800.0, max(5.0, cooldown * (2 if previous_state == "half_open" else 1)))
                opened_until = current + cooldown * random.uniform(0.9, 1.1)
            connection.execute(
                """
                INSERT INTO model_provider_health(
                  provider_id, state, consecutive_failures, opened_until,
                  last_failure_class, last_failure_at, adaptive_concurrency,
                  cooldown_seconds, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                  state = excluded.state,
                  consecutive_failures = excluded.consecutive_failures,
                  opened_until = excluded.opened_until,
                  last_failure_class = excluded.last_failure_class,
                  last_failure_at = excluded.last_failure_at,
                  adaptive_concurrency = excluded.adaptive_concurrency,
                  cooldown_seconds = excluded.cooldown_seconds,
                  updated_at = excluded.updated_at
                """,
                (
                    provider_id,
                    state,
                    failures,
                    opened_until or None,
                    failure_class,
                    utc_now(),
                    max(1, int(adaptive)),
                    cooldown,
                    utc_now(),
                ),
            )
            result = connection.execute(
                "SELECT * FROM model_provider_health WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
        return {**dict(result), "now": current}

    def model_provider_recent_usage(
        self,
        provider_id: str,
        *,
        since: float,
    ) -> dict[str, Any]:
        since_text = datetime.fromtimestamp(
            float(since),
            timezone.utc,
        ).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS requests,
                       COALESCE(SUM(estimated_tokens), 0) AS estimated_tokens,
                       MIN(created_at) AS oldest
                FROM model_calls
                WHERE provider_id = ? AND created_at >= ?
                """,
                (provider_id, since_text),
            ).fetchone()
        return dict(row)

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
