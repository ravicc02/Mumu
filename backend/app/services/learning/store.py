from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from ...db import Database, utc_now


ACTIVE = {"queued", "running", "paused", "awaiting_user", "stopping"}
TRANSITIONS = {
    "queued": {"running", "paused", "stopping", "stopped", "interrupted"},
    "running": {"paused", "awaiting_user", "stopping", "completed", "incomplete", "interrupted"},
    "paused": {"queued", "running", "stopping", "interrupted"},
    "awaiting_user": {"running", "stopping", "interrupted"},
    "stopping": {"stopped", "interrupted"},
    "completed": set(), "incomplete": set(), "interrupted": set(), "stopped": set(),
}


class LearningStore:
    def __init__(self, database: Database):
        self.database = database

    def initialize(self):
        with self.database.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS learning_courses (
                  id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                  platform TEXT NOT NULL, upstream_id TEXT NOT NULL, class_id TEXT NOT NULL,
                  name TEXT NOT NULL, academic_year INTEGER NOT NULL, semester TEXT, url TEXT NOT NULL,
                  chapters_json TEXT NOT NULL DEFAULT '[]', verified_at TEXT, updated_at TEXT NOT NULL,
                  active INTEGER NOT NULL DEFAULT 1,
                  UNIQUE(account_id,platform,upstream_id,class_id)
                );
                CREATE TABLE IF NOT EXISTS learning_runs (
                  id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES learning_courses(id) ON DELETE CASCADE,
                  account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, status TEXT NOT NULL,
                  phase TEXT NOT NULL DEFAULT 'queued', progress INTEGER NOT NULL DEFAULT 0,
                  current_chapter TEXT, current_unit TEXT, position REAL DEFAULT 0, duration REAL DEFAULT 0,
                  target_json TEXT NOT NULL DEFAULT '[]', completed_json TEXT NOT NULL DEFAULT '[]',
                  error TEXT, attempt INTEGER NOT NULL DEFAULT 1,
                  auto_retry_count INTEGER NOT NULL DEFAULT 0, parent_id TEXT,
                  paused_from TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  started_at TEXT, finished_at TEXT, heartbeat_at TEXT,
                  engine_version TEXT NOT NULL DEFAULT 'mumu-learning-engine-v1'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_active_course ON learning_runs(course_id)
                  WHERE status IN ('queued','running','paused','awaiting_user','stopping');
                CREATE TABLE IF NOT EXISTS learning_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES learning_runs(id) ON DELETE CASCADE,
                  kind TEXT NOT NULL, message TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_artifacts (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES learning_runs(id) ON DELETE CASCADE,
                  kind TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(learning_runs)")}
            if "headless" not in columns:
                connection.execute("ALTER TABLE learning_runs ADD COLUMN headless INTEGER NOT NULL DEFAULT 0")
            if "deleted_at" not in columns:
                connection.execute("ALTER TABLE learning_runs ADD COLUMN deleted_at TEXT")
            if "auto_retry_count" not in columns:
                connection.execute("ALTER TABLE learning_runs ADD COLUMN auto_retry_count INTEGER NOT NULL DEFAULT 0")

    def upsert_courses(self, account_id: str, courses: list[dict], year: int, *, replace=True):
        identifiers = []
        platform = self.database.get_account(account_id)["platform"]
        with self.database.connect() as connection:
            for item in courses:
                key = f"{account_id}:{platform}:{item['courseId']}:{item['classId']}"
                identifier = hashlib.sha256(key.encode()).hexdigest()[:32]
                identifiers.append(identifier)
                connection.execute("""INSERT INTO learning_courses
                    (id,account_id,platform,upstream_id,class_id,name,academic_year,semester,url,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,academic_year=excluded.academic_year,semester=excluded.semester,url=excluded.url,active=1,updated_at=excluded.updated_at""",
                    (identifier, account_id, platform, item["courseId"], item["classId"], item["courseName"], year,
                     item.get("semester"), item["url"], utc_now()))
            if identifiers and replace:
                marks = ','.join('?' for _ in identifiers)
                connection.execute(f"UPDATE learning_courses SET active=0 WHERE account_id=? AND academic_year=? AND id NOT IN ({marks})", (account_id, year, *identifiers))
        return self.courses(account_id, year)

    def courses(self, account_id: str, year: int):
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM learning_courses WHERE account_id=? AND academic_year=? AND active=1 ORDER BY name", (account_id, year)).fetchall()
        return [self._course(dict(row)) for row in rows]

    def course(self, identifier: str):
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM learning_courses WHERE id=?", (identifier,)).fetchone()
        if not row: raise KeyError("课程不存在，请先采集")
        return self._course(dict(row))

    def _course(self, row):
        row["chapters"] = json.loads(row.pop("chapters_json"))
        return row

    def save_chapters(self, identifier: str, chapters: list):
        if not chapters: raise ValueError("章节目录为空，无法确认课程进度")
        with self.database.connect() as connection:
            connection.execute("UPDATE learning_courses SET chapters_json=?,verified_at=?,updated_at=? WHERE id=?", (json.dumps(chapters, ensure_ascii=False), utc_now(), utc_now(), identifier))

    def create_run(self, course_id: str, *, headless=True):
        course = self.course(course_id)
        identifier = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute("SELECT id FROM learning_runs WHERE course_id=? AND status IN ('queued','running','paused','awaiting_user','stopping')", (course_id,)).fetchone()
            if active: raise RuntimeError("该课程已有执行任务")
            previous = connection.execute("SELECT id,attempt FROM learning_runs WHERE course_id=? ORDER BY attempt DESC LIMIT 1", (course_id,)).fetchone()
            connection.execute("INSERT INTO learning_runs(id,course_id,account_id,status,attempt,parent_id,created_at,updated_at,engine_version,headless) VALUES (?,?,?,'queued',?,?,?,?,'mumu-learning-resources-v1',?)", (identifier, course_id, course["account_id"], previous["attempt"] + 1 if previous else 1, previous["id"] if previous else None, utc_now(), utc_now(), int(headless)))
        run = self.run(identifier)
        self.database.diagnostics.start("learning", identifier, run, self.database.get_account(course["account_id"]))
        return run

    def run(self, identifier: str):
        with self.database.connect() as connection:
            row = connection.execute("""SELECT r.*,c.name course_name,c.url,c.platform,a.name account_name
                FROM learning_runs r JOIN learning_courses c ON c.id=r.course_id JOIN accounts a ON a.id=r.account_id WHERE r.id=?""", (identifier,)).fetchone()
        if not row: raise KeyError("网课任务不存在")
        result = dict(row)
        result["target"] = json.loads(result.pop("target_json"))
        result["completed"] = json.loads(result.pop("completed_json"))
        return result

    def runs(self, account_id: str | None = None, *, include_deleted=False):
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id FROM learning_runs WHERE (? OR deleted_at IS NULL) " + ("AND account_id=? " if account_id else "") + "ORDER BY julianday(created_at) DESC, rowid DESC", (include_deleted, account_id) if account_id else (include_deleted,)).fetchall()
        return [self.run(row["id"]) for row in rows]

    def delete_task(self, identifier):
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT account_id,course_id FROM learning_runs WHERE id=?", (identifier,)).fetchone()
            if not run: raise KeyError("网课任务不存在")
            attempts = connection.execute("SELECT id,status FROM learning_runs WHERE account_id=? AND course_id=?", (run["account_id"],run["course_id"])).fetchall()
            if any(row["status"] in ACTIVE for row in attempts):
                raise RuntimeError("请先停止网课任务，再删除任务。")
            connection.execute("UPDATE learning_runs SET deleted_at=? WHERE account_id=? AND course_id=? AND deleted_at IS NULL", (utc_now(),run["account_id"],run["course_id"]))
        return {"deleted_ids": [row["id"] for row in attempts], "task_kind": "learning"}

    def active(self, account_id: str):
        # Some unit-test databases predate this additive schema.
        with self.database.connect() as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_runs'").fetchone(): return None
        candidates = [run for run in self.runs(account_id) if run["status"] in ACTIVE]
        return next((run for run in candidates if run["status"] != "queued"), candidates[-1] if candidates else None)

    def update(self, identifier: str, **fields):
        trace = self.database.diagnostics.trace("learning", identifier)
        if trace.available and fields.get("error"): fields["error"] = trace.scrubber.text(fields["error"])
        allowed = {"status", "phase", "progress", "current_chapter", "current_unit", "position", "duration", "target_json", "completed_json", "error", "auto_retry_count", "paused_from", "started_at", "finished_at", "heartbeat_at"}
        if not fields or not set(fields) <= allowed: raise ValueError("无效网课任务字段")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM learning_runs WHERE id=?", (identifier,)).fetchone()
            if not row: raise KeyError("网课任务不存在")
            status = fields.get("status", row["status"])
            if status != row["status"] and status not in TRANSITIONS[row["status"]]: raise RuntimeError(f"不能从 {row['status']} 转到 {status}")
            if status == "completed" and row["status"] != "completed" and fields.get("phase") != "verified": raise RuntimeError("完成状态需要平台核验")
            fields["updated_at"] = utc_now()
            connection.execute(f"UPDATE learning_runs SET {','.join(f'{key}=?' for key in fields)} WHERE id=?", (*fields.values(), identifier))

    def event(self, identifier, kind, message, data=None):
        trace = self.database.diagnostics.trace("learning", identifier)
        if trace.available:
            message, data = trace.prepare_event(message, data)
        now = utc_now()
        with self.database.connect() as connection:
            cursor = connection.execute("INSERT INTO learning_events(run_id,kind,message,data_json,created_at) VALUES (?,?,?,?,?)", (identifier, kind, message, json.dumps(data or {}, ensure_ascii=False), now))
            event_id = cursor.lastrowid
        trace = self.database.diagnostics.trace("learning", identifier)
        if trace.available:
            run = self.run(identifier)
            trace.record("learning." + kind, message, data, state=run["status"], phase=run.get("phase"), source_event_id=event_id)
            trace.summary(run)
        return {"id": event_id, "kind": kind, "message": message, "data": data or {}, "created_at": now}

    def events(self, identifier, after_id=0):
        self.run(identifier)
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM learning_events WHERE run_id=? AND id>? ORDER BY id", (identifier, after_id)).fetchall()
        return [{**dict(row), "data": json.loads(row["data_json"])} for row in rows]

    def add_artifact(self, identifier, path, kind="screenshot"):
        artifact = {"id": uuid.uuid4().hex, "run_id": identifier, "path": str(path), "kind": kind, "created_at": utc_now()}
        with self.database.connect() as connection:
            connection.execute("INSERT INTO learning_artifacts VALUES (?,?,?,?,?)", tuple(artifact[key] for key in ("id", "run_id", "kind", "path", "created_at")))
        return artifact

    def artifacts(self, identifier):
        self.run(identifier)
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM learning_artifacts WHERE run_id=? ORDER BY created_at", (identifier,))]

    def recover(self):
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id,status,auto_retry_count FROM learning_runs WHERE status IN ('queued','running','paused','awaiting_user','stopping')").fetchall()
        for row in rows:
            manual = row["status"] in {"paused", "awaiting_user", "stopping"}
            reason = (
                f"本地服务重启，原任务状态为 {row['status']}，需要人工确认。"
                if manual else
                "本地服务重启，原浏览器会话已结束，将自动重新读取平台进度。"
            )
            self.update(
                row["id"], status="interrupted",
                phase="interrupted" if manual else "auto_retry_pending",
                error=reason, finished_at=utc_now(),
            )
            self.event(row["id"], "interrupted", "本地服务重启，任务已中断")
        return [dict(row) for row in rows]

    def retryable_interrupted(self, limit=3):
        with self.database.connect() as connection:
            rows = connection.execute("""SELECT id,error,auto_retry_count FROM learning_runs
              WHERE status='interrupted' AND phase='auto_retry_pending' AND auto_retry_count<?
              ORDER BY julianday(updated_at),rowid""", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def reopen_for_retry(self, identifier, reason='', limit=3):
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status,auto_retry_count FROM learning_runs WHERE id=?",
                (identifier,),
            ).fetchone()
            if not row:
                raise KeyError("网课任务不存在")
            if row["status"] != "interrupted":
                raise RuntimeError("只有已中断的网课任务可以自动重试")
            retry_count = int(row["auto_retry_count"] or 0)
            if retry_count >= limit:
                raise RuntimeError("自动重试次数已用完")
            retry_count += 1
            connection.execute("""UPDATE learning_runs SET status='queued',phase='queued',error=NULL,
              finished_at=NULL,started_at=NULL,heartbeat_at=NULL,paused_from=NULL,
              auto_retry_count=?,updated_at=? WHERE id=?""",
              (retry_count, utc_now(), identifier))
        return self.run(identifier)
