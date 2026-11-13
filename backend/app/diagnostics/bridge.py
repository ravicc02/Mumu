"""Incrementally import legacy logs, including an old backend still serving a task."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .daily import daily_log, parse_timestamp
from .privacy import SECRET_KEYS, normalize_key, task_scrubber


def legacy_scrubber(settings):
    scrubber = task_scrubber(settings)
    if not settings.database_path.is_file(): return scrubber
    try:
        with closing(sqlite3.connect(settings.database_path.as_uri() + "?mode=ro", uri=True)) as database:
            tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "accounts" in tables:
                for name, path in database.execute("SELECT name,storage_state_path FROM accounts"):
                    account = {"name": name}
                    candidate = Path(path) if path else None
                    if candidate and candidate.is_file() and not candidate.is_symlink() and candidate.resolve().is_relative_to(settings.profiles_root.resolve()) and candidate.stat().st_size <= 4 * 1024 * 1024:
                        account["storage_state_path"] = path
                    private = task_scrubber(settings, account)
                    scrubber.replacements.extend(private.replacements)
                    scrubber.identities.extend(private.identities)
            if "app_settings" in tables:
                for row in database.execute("SELECT value FROM app_settings WHERE key IN ('model_config','weixin_binding')"):
                    try: value = json.loads(row[0])
                    except ValueError: continue
                    if isinstance(value, dict):
                        for key, secret in value.items():
                            if isinstance(secret, str) and secret and (normalize_key(key) in SECRET_KEYS or normalize_key(key).endswith(("token", "apikey"))):
                                scrubber.replacements.append((secret, "[REDACTED]"))
    except (OSError, sqlite3.Error):
        pass
    scrubber.replacements = sorted(set(scrubber.replacements), key=lambda item: len(item[0]), reverse=True)
    scrubber.identities = list(set(scrubber.identities))
    return scrubber


class LegacyLogImporter:
    def __init__(self, settings, journal=None, *, extra_sources=()):
        self.settings = settings
        self.journal = journal or daily_log(settings)
        self.scrubber = legacy_scrubber(settings)
        self.errors = set()
        self.extra_sources = {}
        self.context_cache = {}
        for path in extra_sources:
            path = Path(path)
            if path.is_symlink(): raise ValueError("Legacy stream cannot be a symbolic file link")
            self.extra_sources[path.resolve()] = "launcher/" + path.name

    def source_name(self, path):
        return self.extra_sources.get(path.resolve()) or path.relative_to(self.settings.logs_root).as_posix()

    def task_context(self, trace_id, kind):
        if not isinstance(trace_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", trace_id): return None
        day = self.journal.clock().date().isoformat()
        key = (trace_id, day)
        if key in self.context_cache: return self.context_cache[key]
        for candidate in ([kind] if kind in {"assignment", "learning"} else ["assignment", "learning"]):
            path = self.settings.logs_root / "diagnostics" / "v1" / candidate / trace_id / "manifest.json"
            if path.is_symlink() or not path.resolve().is_relative_to(self.settings.logs_root.resolve()): continue
            try: value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError): continue
            if isinstance(value, dict):
                self.context_cache[key] = value
                return value
        return None

    def sources(self):
        root = self.settings.logs_root
        for path in self.extra_sources:
            if path.is_file(): yield path, "lines", None
        for name in ("backend.jsonl.3", "backend.jsonl.2", "backend.jsonl.1", "backend.jsonl", "backend.log"):
            path = root / name
            if path.is_file(): yield path, "lines", None
        for kind in ("assignment", "learning"):
            parent = root / "diagnostics" / "v1" / kind
            for folder in sorted(parent.glob("*")):
                if folder.is_symlink() or not folder.is_dir() or not folder.resolve().is_relative_to(root.resolve()): continue
                manifest = folder / "manifest.json"
                try: metadata = json.loads(manifest.read_text(encoding="utf-8")) if not manifest.is_symlink() else {}
                except (OSError, ValueError): metadata = {}
                if not isinstance(metadata, dict): metadata = {}
                cutoff = parse_timestamp(metadata.get("main_log_started_at"))
                task_logs = [folder / name for name in ("task.2.log", "task.1.log", "task.log") if (folder / name).is_file()]
                if task_logs:
                    for path in task_logs: yield path, "lines", None
                else:
                    for name in ("events.2.jsonl", "events.1.jsonl", "events.jsonl"):
                        path = folder / name
                        if path.is_file(): yield path, "lines", cutoff
                    for path in sorted((folder / "evidence").glob("*.json")): yield path, "snapshot", cutoff
                if manifest.is_file() and not cutoff: yield manifest, "context", None

    def poll(self, *, limit=2000, final=False):
        consumed = 0
        for path, mode, cutoff in self.sources():
            if consumed >= limit: break
            if path.is_symlink() or (path.resolve() not in self.extra_sources and not path.resolve().is_relative_to(self.settings.logs_root.resolve())): continue
            try:
                stat = path.stat()
                key = f"{stat.st_dev}:{stat.st_ino}" if stat.st_ino else str(path.relative_to(self.settings.logs_root)) + ":" + str(stat.st_ctime_ns)
                cursor = self.journal.source_cursor(key)
                start = cursor.get("offset", 0)
                if stat.st_size < start:
                    key += ":truncated:" + str(stat.st_ctime_ns)
                    start = self.journal.source_offset(key)
                if start >= stat.st_size: continue
                relative = self.source_name(path)
                last_stamp = cursor.get("last_timestamp") or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
                rows, end = [], start
                with path.open("rb") as source:
                    source.seek(start)
                    while consumed < limit:
                        offset = source.tell()
                        raw = source.read(3 * 1024 * 1024 + 1) if mode != "lines" else source.readline(3 * 1024 * 1024 + 1)
                        if not raw: break
                        if mode == "lines" and not raw.endswith(b"\n") and len(raw) < 3 * 1024 * 1024 and not final and time.time() - stat.st_mtime < 10:
                            break
                        end = source.tell(); consumed += 1
                        try: text = raw.decode("utf-8-sig")
                        except UnicodeDecodeError: text = raw.decode("gb18030", errors="replace")
                        try: value = json.loads(text)
                        except ValueError:
                            match = re.search(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?", text)
                            last_stamp = match[0] if match else last_stamp
                            value = {"code": "legacy.text", "message": text.rstrip(), "timestamp": last_stamp, "date_inferred": not bool(match)}
                        if not isinstance(value, dict): value = {"data": value}
                        stamp = value.get("timestamp") or value.get("captured_at") or value.get("updated_at") or last_stamp
                        last_stamp = stamp
                        parsed = parse_timestamp(stamp)
                        if cutoff and parsed:
                            if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=self.journal.clock().tzinfo)
                            if cutoff.tzinfo is None: cutoff = cutoff.replace(tzinfo=self.journal.clock().tzinfo)
                            if parsed >= cutoff: continue
                        if mode in {"snapshot", "context"}:
                            parts = relative.split("/")
                            value = {"trace_id": parts[3] if len(parts) > 3 else None, "task_kind": parts[2] if len(parts) > 2 else None,
                                "code": "diagnostics.evidence" if mode == "snapshot" else "legacy.task_context", "data": value}
                        value = {**value, "timestamp": stamp, "imported_from": relative}
                        value.setdefault("event_id", hashlib.sha256((key + ":" + str(offset)).encode()).hexdigest())
                        if len(raw) > 3 * 1024 * 1024:
                            value = {"timestamp": stamp, "code": "legacy.record_omitted", "imported_from": relative, "data": {"reason": "legacy_record_size_limit"}}
                        rows.append(value)
                        if mode != "lines": break
                if end > start:
                    contexts = {}
                    for row in rows:
                        context = self.task_context(row.get("trace_id"), row.get("task_kind"))
                        if context: contexts[row["trace_id"]] = context
                    self.journal.import_batch(key, start, end, rows, source=relative, scrubber=self.scrubber, last_timestamp=last_stamp, contexts=contexts)
            except (OSError, ValueError, TypeError) as error:
                key = str(path)
                if key not in self.errors:
                    self.errors.add(key)
                    try: self.journal.write({"code": "legacy.import_unavailable", "level": "warning", "message": "Legacy log will be retried", "data": {"file": self.source_name(path), "error_type": type(error).__name__}})
                    except (OSError, ValueError, TypeError): pass
        return consumed


def follow_process(pid, importer):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        alive = lambda: bool(handle) and kernel.WaitForSingleObject(handle, 0) == 258
    else:
        handle = None
        def alive():
            try: os.kill(pid, 0); return True
            except ProcessLookupError: return False
    try:
        while alive():
            importer.journal.prepare()
            count = importer.poll()
            time.sleep(.1 if count >= 2000 else 1)
        while importer.poll(final=True): pass
    finally:
        if handle: kernel.CloseHandle(handle)


def main():
    from ..config import Settings
    parser = argparse.ArgumentParser(description="Import legacy logs into the daily main.log")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--follow-pid", type=int)
    parser.add_argument("--legacy-stream", type=Path, action="append", default=[])
    args = parser.parse_args()
    settings = Settings()
    if args.data_root: settings = dataclasses.replace(settings, data_root=args.data_root.resolve())
    importer = LegacyLogImporter(settings, extra_sources=args.legacy_stream)
    importer.journal.prepare()
    if args.follow_pid:
        importer.journal.write({"code":"log.compatibility_bridge","level":"info","component":"logging",
            "message":"Legacy backend logs are being collected without restarting its tasks",
            "data":{"source_backend_pid":args.follow_pid,"collector_pid":os.getpid(),"backend_code_reloaded":False}})
        print("Daily log compatibility bridge started", flush=True)
        follow_process(args.follow_pid, importer)
    else:
        while importer.poll(final=True): pass
        print("Daily log import complete", flush=True)


if __name__ == "__main__": main()
