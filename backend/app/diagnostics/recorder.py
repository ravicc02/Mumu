from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .privacy import Scrubber, auth_summary, task_scrubber
from .daily import daily_log
from ..version import __version__

SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 65536
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
ROTATE_BYTES = 2 * 1024 * 1024
logger = logging.getLogger("answer-studio.diagnostics")
LOCK = threading.RLock()
FINAL = {"completed", "stopped", "interrupted", "failed", "incomplete", "needs_intervention"}


def now(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
def encoded(value): return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str).encode("utf-8", errors="backslashreplace")


def environment():
    build_file = Path(__file__).with_name("diagnostic-build.json")
    build = json.loads(build_file.read_text(encoding="utf-8")) if build_file.is_file() else {}
    versions = {}
    for name in ("browser-use", "playwright", "fastapi", "pydantic", "httpx"):
        try: versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name] = "unavailable"
    root = Path(__file__).parents[1]
    hashes = {}
    for relative in ("services/agent_runner.py", "services/quiz_adapter.py", "services/learning/runtime.js", "services/learning/ocs-study.js", "services/learning/platform-runtime.js", "services/learning/multi_platform.py", "services/platforms/catalog.json", "services/educoder/client.py", "services/educoder/contracts.py", "services/educoder/executor.py", "services/task_spec.py", "services/educoder/code_contract.py", "services/educoder/evaluation.py", "services/educoder/adapter.py", "services/account_identity.py", "diagnostics/recorder.py", "diagnostics/probe.py", "diagnostics/daily.py", "diagnostics/bridge.py", "diagnostics/logging.py", "diagnostics/privacy.py", "diagnostics/layout.py"):
        path = root / relative
        if path.is_file(): hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    version = __version__
    return {"app_version": version, "build": build, "local_timezone": str(datetime.now().astimezone().tzinfo), "python": platform.python_version(), "os": platform.system(), "os_release": platform.release(),
            "architecture": platform.machine(), "frozen": bool(getattr(sys, "frozen", False)), "dependencies": versions,
            "source_hashes": build.get("source_hashes", hashes), "build_fingerprint": build.get("build_fingerprint") or hashlib.sha256(encoded(hashes)).hexdigest()[:20]}


class NullTrace:
    available = False
    def configure(self, *args, **kwargs): pass
    def record(self, *args, **kwargs): pass
    def snapshot(self, *args, **kwargs): pass
    def exception(self, *args, **kwargs): pass
    def summary(self, *args, **kwargs): pass
    def prepare_event(self, message, data): return message, data
    @contextmanager
    def span(self, *args, **kwargs): yield


class Trace(NullTrace):
    available = True

    def __init__(self, folder, kind, identifier, settings):
        self.folder, self.kind, self.identifier, self.settings = folder, kind, identifier, settings
        self.scrubber = task_scrubber(settings)
        self.started = time.monotonic()
        self.sequence = 0
        self.failed = False
        self.manifest = {}
        if (folder / "manifest.json").is_file():
            if (folder / "manifest.json").is_symlink(): raise ValueError("Invalid diagnostic manifest path")
            self.manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(self.manifest, dict): raise ValueError("Invalid diagnostic manifest")
            self.sequence = int(self.manifest.get("last_sequence", 0))
        self.manifest.setdefault("main_log_started_at", now())
        for path in (folder / "events.jsonl", folder / "events.1.jsonl"):
            if not path.is_file(): continue
            if path.is_symlink() or path.stat().st_size > 3 * 1024 * 1024: raise ValueError("Invalid diagnostic journal")
            raw = path.read_bytes()
            for line in reversed(raw.splitlines()):
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict): continue
                    self.sequence = max(self.sequence, int(record.get("sequence", 0)))
                    break
                except (ValueError, TypeError): continue
            if raw and not raw.endswith(b"\n"):
                with path.open("ab") as output: output.write(b"\n")

    def _write_json(self, name, value):
        target = self.folder / name
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(encoded(value)); os.replace(temp, target)

    def _log_entry(self, code, data):
        return {"schema_version": SCHEMA_VERSION, "timestamp": now(), "trace_id": self.identifier,
            "task_kind": self.kind, "process_session": self.manifest.get("process_session"),
            "code": code, "data": data}

    def _append_task_log(self, value):
        daily_log(self.settings).write(value, context=self.manifest, scrubber=self.scrubber)


    def _safe(self, callback):
        try:
            with LOCK: return callback()
        except (OSError, ValueError, TypeError) as error:
            already_failed = self.failed
            self.failed = True
            if not already_failed: logger.error("Diagnostic storage unavailable for %s: %s", self.identifier, type(error).__name__)

    def configure(self, task, account, settings):
        def write():
            self.scrubber = task_scrubber(settings, account)
            self.folder.mkdir(parents=True, exist_ok=True)
            self.manifest.update({"schema_version": SCHEMA_VERSION, "trace_id": self.identifier, "task_kind": self.kind,
                "created_at": self.manifest.get("created_at", now()), "environment": self.scrubber.clean(environment()),
                "task": self.scrubber.clean(task), "authentication": self.scrubber.clean(auth_summary(account)),
                "model": self.scrubber.clean({"base_url": settings.openai_base_url, "model": settings.openai_model,
                    "fallback_model": settings.openai_fallback_model, "reasoning_effort": settings.reasoning_effort}),
                "limits": {"event_bytes": MAX_EVENT_BYTES, "evidence_bytes": MAX_EVIDENCE_BYTES, "rotation_bytes": ROTATE_BYTES, "rotation_files": 3},
                "log_storage": {"current": "main.log", "archive": "oldmain.log", "rotation": "local_calendar_day"},
                "process_session": uuid.uuid4().hex, "last_sequence": self.sequence})
            self._write_json("manifest.json", self.manifest)
            self._append_task_log(self._log_entry("task.context", self.manifest))
        self._safe(write)

    def record(self, code, message="", data=None, *, component=None, level=None, phase=None, state=None, source_event_id=None):
        def write():
            self.folder.mkdir(parents=True, exist_ok=True)
            self.sequence += 1
            value = self.scrubber.clean({"schema_version": SCHEMA_VERSION, "trace_id": self.identifier, "sequence": self.sequence,
                "event_id": uuid.uuid4().hex, "timestamp": now(), "elapsed_ms": round((time.monotonic() - self.started) * 1000),
                "process_session": self.manifest.get("process_session"), "component": component or self.kind,
                "code": code, "level": level or ("error" if code.split(".")[-1] in {"error", "interrupted", "incomplete", "failed", "notification_error", "quiz_verification_failed"} else "info"),
                "phase": phase or self.manifest.get("phase"), "state": state or self.manifest.get("state"), "source_event_id": source_event_id, "message": message, "data": data or {}})
            raw = encoded(value)
            if len(raw) > MAX_EVENT_BYTES:
                value["data"] = {"omitted": "event_size_limit", "original_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                value["message"] = value["message"][:2000]; raw = encoded(value)
            path = self.folder / "events.jsonl"
            if path.exists() and path.stat().st_size + len(raw) > ROTATE_BYTES:
                oldest = self.folder / "events.2.jsonl"
                oldest.unlink(missing_ok=True)
                previous = self.folder / "events.1.jsonl"
                if previous.exists(): os.replace(previous, oldest)
                os.replace(path, previous)
                self.manifest["events_rotated"] = True
            with path.open("ab") as output: output.write(raw + b"\n")
            self._append_task_log(value)
            self.manifest.update(last_sequence=self.sequence, updated_at=value["timestamp"])
            if state: self.manifest["state"] = state
            self._write_json("manifest.json", self.manifest)
        self._safe(write)

    def prepare_event(self, message, data):
        message, data = self.scrubber.text(message), self.scrubber.clean(data or {})
        if len(encoded({"message": message, "data": data})) > MAX_EVENT_BYTES:
            self.snapshot("oversized-ui-event", {"message": message, "data": data}, content=True)
            retained = {key: value for key, value in data.items() if key in {"status", "phase", "step", "unit_id", "filled_count", "total_units", "question_count", "unit_count", "artifact_id"}}
            retained.update(omitted="event_size_limit", original_bytes=len(encoded(data)))
            return message[:8000] + "[TRUNCATED]", retained
        return message, data

    def snapshot(self, name, data, *, content=False):
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", name): raise ValueError("Invalid diagnostic snapshot name")
        def write():
            folder = self.folder / "evidence"; folder.mkdir(exist_ok=True, parents=True)
            value = {"schema_version": SCHEMA_VERSION, "trace_id": self.identifier, "captured_at": now(), "content": content, "data": self.scrubber.clean(data)}
            raw = encoded(value)
            if len(raw) > MAX_EVIDENCE_BYTES:
                value["data"] = {"omitted": "evidence_size_limit", "original_bytes": len(raw)}
            budget = (12 if name.startswith("failure") else 8) * 1024 * 1024
            if sum(path.stat().st_size for path in folder.glob("*.json")) + len(encoded(value)) > budget:
                self.record("diagnostics.evidence_omitted", "Diagnostic evidence budget reached", {"snapshot": name, "reason": "trace_size_limit"}, component="diagnostics", level="warning")
                return
            relative = f"evidence/{self.sequence + 1:06d}-{name}.json"
            self._write_json(relative, value)
            self.record("diagnostics.snapshot", f"Captured {name}", {"file": relative, "content": content}, component="diagnostics")
            self._append_task_log(self._log_entry("diagnostics.evidence", {"name": name, "file": relative, **value}))
        self._safe(write)

    def exception(self, error, *, phase=None, data=None):
        causes, current, response = [], error, None
        for _ in range(4):
            if current is None: break
            causes.append({"type": type(current).__name__, "message": str(current)})
            response = response or getattr(current, "response", None)
            current = current.__cause__ or current.__context__
        headers = getattr(response, "headers", {}) or {}
        self.record("task.exception", str(error), {"exception_type": type(error).__name__, "causes": causes,
            "frames": [{"file": item.filename, "line": item.lineno, "function": item.name} for item in traceback.extract_tb(error.__traceback__)],
            "stack": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            "http_status": getattr(response, "status_code", None), "request_id": headers.get("x-request-id") or headers.get("request-id"),
            **(data or {})}, component="executor", level="error", phase=phase)

    def summary(self, task):
        def write():
            self.manifest.update(state=task.get("status"), phase=task.get("phase"), browser_retained=bool(task.get("browser_retained")), updated_at=now())
            self._write_json("summary.json", self.scrubber.clean(task))
            self._write_json("manifest.json", self.manifest)
            self._append_task_log(self._log_entry("task.summary", self.scrubber.clean(task)))
        self._safe(write)

    @contextmanager
    def span(self, code, data=None):
        span_id, start = uuid.uuid4().hex, time.monotonic()
        self.record(code + ".started", data={"span_id": span_id, **(data or {})})
        try: yield
        except BaseException as error:
            self.record(code + ".failed", str(error), {"span_id": span_id, "duration_ms": round((time.monotonic() - start) * 1000), "exception_type": type(error).__name__}, level="error")
            raise
        else: self.record(code + ".finished", data={"span_id": span_id, "duration_ms": round((time.monotonic() - start) * 1000)})


class DiagnosticStore:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.logs_root / "diagnostics" / "v1"
        self.traces = {}

    def folder(self, kind, identifier):
        if kind not in {"assignment", "learning"} or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identifier): raise ValueError("Invalid diagnostic identity")
        path = self.root / kind / identifier
        if not path.resolve().is_relative_to(self.root.resolve()): raise ValueError("Invalid diagnostic path")
        return path

    def trace(self, kind, identifier):
        key = (kind, identifier)
        if key not in self.traces:
            folder = self.folder(kind, identifier)
            if not (folder / "manifest.json").is_file(): return NullTrace()
            try: self.traces[key] = Trace(folder, kind, identifier, self.settings)
            except (OSError, ValueError, TypeError):
                logger.error("Diagnostic journal could not be reopened for %s", identifier)
                return NullTrace()
        return self.traces[key]

    def start(self, kind, identifier, task, account):
        try:
            self.prune()
            trace = Trace(self.folder(kind, identifier), kind, identifier, self.settings)
        except (OSError, ValueError, TypeError):
            logger.error("Diagnostic journal could not be created for %s", identifier)
            return NullTrace()
        trace.configure(task, account, self.settings)
        self.traces[(kind, identifier)] = trace
        trace.record("task.created", "Task diagnostics started", state=task.get("status"))
        return trace

    def prune(self, *, days=None, total_bytes=None, protected=()):
        provider = getattr(self, "retention_provider", None)
        retention = provider() if provider else None
        if days is None: days = retention.log_retention_days if retention else 14
        if total_bytes is None: total_bytes = (retention.log_size_mb if retention else 200) * 1024 * 1024
        with LOCK: return self._prune(days=days, total_bytes=total_bytes, protected=protected)

    def _prune(self, *, days, total_bytes, protected):
        candidates, total = [], 0
        if not self.root.exists(): return
        for kind in ("assignment", "learning"):
            for path in (self.root / kind).glob("*"):
                if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()): continue
                try:
                    meta = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
                    size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())
                    total += size
                    if meta.get("state") not in FINAL or meta.get("browser_retained") or path.name in protected: continue
                    age = datetime.fromisoformat(meta.get("updated_at", meta["created_at"])).timestamp()
                    candidates.append((age, size, path))
                except (OSError, ValueError, KeyError): continue
        for age, size, path in sorted(candidates):
            if time.time() - age > days * 86400 or total > total_bytes:
                if path.resolve().is_relative_to(self.root.resolve()) and not path.is_symlink():
                    shutil.rmtree(path); total -= size
                    self.traces.pop((path.parent.name, path.name), None)
