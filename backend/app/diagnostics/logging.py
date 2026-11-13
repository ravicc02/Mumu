from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, time as day_time, timedelta, timezone

from .daily import daily_log
from .privacy import task_scrubber

current_trace = ContextVar("studio_diagnostic_trace", default=None)
current_log_settings = ContextVar("studio_log_settings", default=None)


class DiagnosticFormatter(logging.Formatter):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.scrubber = task_scrubber(settings)

    def data(self, record):
        trace = current_trace.get()
        scoped = current_log_settings.get()
        scrubber = trace.scrubber if getattr(trace, "available", False) else task_scrubber(scoped) if scoped else self.scrubber
        data = {"schema_version": 1, "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                "event_id": uuid.uuid4().hex, "code": getattr(record, "diagnostic_code", "runtime.log"), "level": record.levelname.lower(), "component": record.name, "message": record.getMessage(),
                "trace_id": getattr(trace, "identifier", None), "task_kind": getattr(trace, "kind", None),
                "source": {"file": record.pathname, "line": record.lineno, "function": record.funcName}}
        if hasattr(record, "diagnostic_data"): data["data"] = record.diagnostic_data
        if record.exc_info: data["stack"] = self.formatException(record.exc_info)
        return scrubber.clean(data)

    def format(self, record):
        return json.dumps(self.data(record), ensure_ascii=False, default=str)


class TaskLogHandler(logging.Handler):
    """Compatibility adapter for callers that explicitly journal runtime events."""
    def emit(self, record):
        trace = current_trace.get()
        if not getattr(trace, "available", False) or getattr(trace, "failed", False): return
        data = {}
        if record.exc_info: data["stack"] = logging.Formatter().formatException(record.exc_info)
        trace.record("runtime.log", record.getMessage(), data, component=record.name, level=record.levelname.lower())


class DailyLogHandler(logging.Handler):
    def __init__(self, settings, *, maintenance=False):
        super().__init__()
        self.settings = settings
        self.journal = daily_log(settings)
        self.setFormatter(DiagnosticFormatter(settings))
        self._studio_diagnostics = True
        self.stopped = threading.Event()
        self.worker = None
        self.last_maintenance_error = None
        self.journal.prepare()
        if maintenance:
            self.worker = threading.Thread(target=self._maintain, name="daily-main-log", daemon=True)
            self.worker.start()

    def emit(self, record):
        try:
            trace = current_trace.get()
            settings = trace.settings if getattr(trace, "available", False) else current_log_settings.get() or self.settings
            context = trace.manifest if getattr(trace, "available", False) else None
            daily_log(settings).write(self.formatter.data(record), context=context)
        except Exception as error:
            # logging.handleError would print the original, potentially private, record.
            sys.stderr.write("main.log storage error: " + type(error).__name__ + "\n")

    def _maintain(self):
        from .bridge import LegacyLogImporter
        importer = LegacyLogImporter(self.settings, self.journal)
        while not self.stopped.is_set():
            try:
                active_day = self.journal.prepare()
                processed = importer.poll()
                current = self.journal.clock()
                if active_day != current.date().isoformat(): continue
                midnight = datetime.combine(current.date() + timedelta(days=1), day_time.min, tzinfo=current.tzinfo)
                delay = .1 if processed >= 2000 else max(.1, min(60, (midnight - current).total_seconds()))
            except Exception as error:
                signature = type(error).__name__
                current = time.monotonic()
                if not self.last_maintenance_error or self.last_maintenance_error[0] != signature or current - self.last_maintenance_error[1] >= 30:
                    sys.stderr.write("main.log maintenance error: " + signature + "\n")
                    self.last_maintenance_error = (signature, current)
                delay = .2 if isinstance(error, OSError) else 1
            self.stopped.wait(delay)

    def close(self):
        self.stopped.set()
        if self.worker and self.worker is not threading.current_thread(): self.worker.join(timeout=5.5)
        super().close()


def configure_logging(settings):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    root = logging.getLogger()
    if any(getattr(handler, "_studio_diagnostics", False) for handler in root.handlers): return
    settings.logs_root.mkdir(parents=True, exist_ok=True)
    from .recorder import environment
    journal = daily_log(settings)
    journal.environment = environment()
    handler = DailyLogHandler(settings, maintenance=True)
    console = logging.StreamHandler(); console.setFormatter(DiagnosticFormatter(settings))
    logging.basicConfig(level=logging.INFO, handlers=[handler, console], force=True)
    logging.captureWarnings(True)
    journal.write({"timestamp": datetime.now(timezone.utc).isoformat(), "code": "app.context", "level": "info", "component": "logging", "data": journal.environment})
