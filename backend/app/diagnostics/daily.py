from __future__ import annotations

import json
import os
import platform
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from .privacy import task_scrubber
from .layout import FORMAT_VERSION, iter_records, render_record, useful_record


def local_now():
    return datetime.now().astimezone()


def parse_timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class DailyMainLog:
    """One local calendar day in main.log; all other days append to oldmain.log."""

    def __init__(self, settings, *, clock=local_now):
        self.settings, self.clock = settings, clock
        self.root = settings.logs_root
        self.main = self.root / "main.log"
        self.archive = self.root / "oldmain.log"
        self.control = self.root / "diagnostics" / "daily-log"
        self.state_path = self.control / "state.json"
        self.staged = self.control / "rollover.log"
        self.lock = threading.RLock()
        self.contexts = set()
        self.context_day = None
        self.environment = {"python": platform.python_version(), "os": platform.system()}
        self.scrubber = task_scrubber(settings)

    @contextmanager
    def locked(self):
        with self.lock:
            self.control.mkdir(parents=True, exist_ok=True)
            if not self.control.resolve().is_relative_to(self.root.resolve()): raise ValueError("Log control directory escaped log root")
            for path in (self.main, self.archive, self.state_path, self.staged, self.control / "writer.lock"):
                if path.is_symlink(): raise ValueError("Log storage must not use symbolic file links")
            with (self.control / "writer.lock").open("a+b") as handle:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        handle.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline: raise TimeoutError("main.log writer is busy")
                        time.sleep(.02)
                try: yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else: fcntl.flock(handle, fcntl.LOCK_UN)

    def _state(self):
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict): raise ValueError("Invalid log state")
            if value.get("active_day"):
                try: date.fromisoformat(value["active_day"])
                except (TypeError, ValueError): value.pop("active_day", None)
            return value
        except (OSError, ValueError):
            if self.staged.exists(): raise ValueError("Rollover state is unavailable; staged logs were preserved")
            return {}

    def _save(self, state):
        temporary = self.control / "state.tmp"
        if temporary.is_symlink(): raise ValueError("Invalid log state path")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(state, output, ensure_ascii=False)
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary, self.state_path)

    def _append(self, path, value):
        raw = render_record(value).encode("utf-8", errors="backslashreplace")
        with path.open("ab+") as output:
            if output.tell():
                output.seek(-1, os.SEEK_END)
                if output.read(1) != b"\n": output.write(b"\n")
            output.seek(0, os.SEEK_END)
            output.write(raw)

    def _upgrade_layout(self, state):
        if state.get("format_version") == FORMAT_VERSION:
            return
        for path in (self.main, self.archive):
            if not path.is_file() or not path.stat().st_size:
                continue
            temporary = self.control / "layout.tmp"
            if temporary.is_symlink():
                raise ValueError("Invalid log layout path")
            with path.open("r", encoding="utf-8", errors="replace") as source, temporary.open("w", encoding="utf-8", newline="\n") as output:
                for value in iter_records(source, preserve_invalid=True):
                    if useful_record(value):
                        output.write(render_record(self.scrubber.clean(value)))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        state["format_version"] = FORMAT_VERSION
        self._save(state)

    def _recover(self, state):
        pending = state.get("pending")
        if self.staged.exists() and not pending and not state.get("active_day"):
            raise ValueError("Unconfirmed rollover file was preserved")
        if pending:
            offset = pending["archive_offset"]
            if not isinstance(offset, int) or offset < 0: raise ValueError("Invalid archive offset")
            date.fromisoformat(pending["target_day"])
            if not self.staged.exists():
                if not self.main.exists(): raise ValueError("Rollover source is missing")
                os.replace(self.main, self.staged)
            with self.archive.open("ab+") as output, self.staged.open("rb") as source:
                if output.tell() < offset: raise ValueError("Archived log was unexpectedly shortened")
                output.truncate(offset); output.seek(0, os.SEEK_END)
                if output.tell():
                    output.seek(-1, os.SEEK_END)
                    if output.read(1) != b"\n": output.write(b"\n")
                    output.seek(0, os.SEEK_END)
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush(); os.fsync(output.fileno())
            state.update(active_day=pending["target_day"], pending=None)
            self._save(state)
        if self.staged.exists(): self.staged.unlink()

    def _prepare(self, current):
        state = self._state()
        self._recover(state)
        self._upgrade_layout(state)
        today = current.date().isoformat()
        day = state.get("active_day")
        if not day and self.main.exists() and self.main.stat().st_size:
            with self.main.open("r", encoding="utf-8") as source:
                first = next(iter_records(source), {})
            stamp = parse_timestamp(first.get("timestamp"))
            day = first.get("local_date") or (stamp.astimezone(current.tzinfo).date().isoformat() if stamp else datetime.fromtimestamp(self.main.stat().st_mtime, current.tzinfo).date().isoformat())
        if day and day != today and self.main.exists() and self.main.stat().st_size:
            state["pending"] = {"archive_offset": self.archive.stat().st_size if self.archive.exists() else 0, "target_day": today}
            self._save(state)
            os.replace(self.main, self.staged)
            self._recover(state)
        if state.get("active_day") != today:
            state["active_day"] = today
            self._save(state)
        if not self.archive.exists(): self.archive.touch()
        if not self.main.exists() or not self.main.stat().st_size:
            self.contexts.clear()
            self._append(self.main, {"schema_version": 1, "timestamp": current.isoformat(), "local_time": current.isoformat(), "local_date": today,
                "event_id": uuid.uuid4().hex, "code": "log.day_started", "level": "info", "component": "logging",
                "message": "Local daily log opened", "data": {"environment": self.scrubber.clean(self.environment), "process_id": os.getpid()}})
        if self.context_day != today:
            self.contexts.clear(); self.context_day = today
        return state

    def prepare(self):
        with self.locked(): return self._prepare(self.clock())["active_day"]

    def _write(self, value, current, *, context=None, scrubber=None):
        safe = (scrubber or self.scrubber).clean(value)
        if not useful_record(safe):
            return
        stamp = parse_timestamp(safe.get("timestamp") or safe.get("captured_at")) or current
        if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=current.tzinfo)
        local = stamp.astimezone(current.tzinfo)
        safe.setdefault("schema_version", 1)
        safe.setdefault("event_id", uuid.uuid4().hex)
        if context and not safe.get("task_kind"): safe["task_kind"] = context.get("task_kind")
        safe.update(timestamp=stamp.isoformat(), local_time=local.isoformat(), local_date=local.date().isoformat())
        target = self.main if local.date() == current.date() else self.archive
        identity = (safe.get("trace_id"), safe.get("process_session"))
        if target == self.main and context and identity[0] and identity not in self.contexts:
            if safe.get("code") != "task.context":
                self._append(self.main, {"schema_version": 1, "timestamp": current.isoformat(), "local_time": current.isoformat(), "local_date": current.date().isoformat(),
                    "event_id": uuid.uuid4().hex, "trace_id": identity[0], "process_session": identity[1], "task_kind": safe.get("task_kind"),
                    "code": "task.context", "data": (scrubber or self.scrubber).clean(context)})
            self.contexts.add(identity)
        self._append(target, safe)

    def write(self, value, *, context=None, scrubber=None):
        with self.locked():
            current = self.clock()
            self._prepare(current)
            self._write(value, current, context=context, scrubber=scrubber)

    def source_offset(self, key):
        return self.source_cursor(key).get("offset", 0)

    def source_cursor(self, key):
        with self.locked(): return dict(self._state().get("sources", {}).get(key, {}))

    def import_batch(self, key, start, end, values, *, source, scrubber=None, last_timestamp=None, contexts=None):
        with self.locked():
            current = self.clock()
            state = self._prepare(current)
            sources = state.setdefault("sources", {})
            if sources.get(key, {}).get("offset", 0) != start: return False
            for value in values:
                trace_id = value.get("trace_id")
                context = contexts.get(trace_id) if contexts and isinstance(trace_id, str) else None
                self._write(value, current, context=context, scrubber=scrubber)
            for path in (self.main, self.archive):
                with path.open("ab") as output: output.flush(); os.fsync(output.fileno())
            sources[key] = {"offset": end, "source": source, "last_timestamp": last_timestamp}
            self._save(state)
            return True


_journals = {}
_journals_lock = threading.Lock()


def daily_log(settings):
    key = str(settings.logs_root.resolve())
    with _journals_lock:
        if key not in _journals: _journals[key] = DailyMainLog(settings)
        return _journals[key]
