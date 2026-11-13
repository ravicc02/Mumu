from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.diagnostics.daily import DailyMainLog
from app.diagnostics.layout import iter_records, render_record, useful_record


class LogLayoutTests(unittest.TestCase):
    def test_readable_block_roundtrip_preserves_correlation_and_multiline_stack(self):
        value = {"local_time": "2026-11-12T19:13:00+08:00", "level": "error", "component": "executor",
                 "code": "task.exception", "trace_id": "task-one", "phase": "verifying",
                 "message": "Verification failed", "stack": "Traceback:\n  adapter.py:42\nValueError: mismatch\n",
                 "data": {"expected": "A", "actual": "", "filled": False, "retries": 0, "controls": []}}
        raw = render_record(value)
        self.assertTrue(raw.startswith("[2026-11-12T19:13:00+08:00] ERROR | executor | task.exception\n"))
        self.assertIn('    "expected": "A"', raw)
        self.assertEqual(list(iter_records(raw.splitlines(keepends=True))), [value])

    def test_empty_top_level_placeholders_are_removed_but_zero_false_and_nested_empty_values_survive(self):
        value = {"code": "step", "message": "", "trace_id": None, "phase": "", "elapsed_ms": 0,
                 "data": {"value": "", "selected": False, "count": 0}}
        row = next(iter_records(render_record(value).splitlines(keepends=True)))
        self.assertNotIn("trace_id", row)
        self.assertNotIn("message", row)
        self.assertEqual(row["elapsed_ms"], 0)
        self.assertEqual(row["data"], value["data"])

    def test_mixed_legacy_and_blocks_and_partial_tail_are_supported(self):
        raw = '  "tail": "partial"\n}\n' + json.dumps({"code": "legacy"}) + "\n" + render_record({"code": "new", "data": {"nested": {"a": 1}}})
        self.assertEqual([row["code"] for row in iter_records(raw.splitlines(keepends=True))], ["legacy", "new"])

    def test_successful_reads_and_heartbeat_are_noise_but_failures_and_mutations_are_never_filtered(self):
        messages = ['127.0.0.1:123 - "GET /api/health HTTP/1.1" 200', "HTTP GET /api/jobs -> 200 request_id=test",
                    '127.0.0.1:123 - "OPTIONS /api/jobs HTTP/1.1" 204 No Content']
        for message in messages:
            self.assertFalse(useful_record({"component": "uvicorn.access", "message": message}))
        self.assertFalse(useful_record({"code": "assignment.heartbeat"}))
        for row in ({"code": "assignment.heartbeat", "level": "warning"},
                    {"component": "uvicorn.access", "message": '"GET /api/jobs HTTP/1.1" 500'},
                    {"component": "answer-studio.api", "message": "HTTP POST /api/jobs -> 200"},
                    {"code": "assignment.heartbeat", "stack": "error stack"},
                    {"code": "api.response", "data": {"http_status": 409}}):
            self.assertTrue(useful_record(row), row)

    def test_legacy_upgrade_filters_noise_keeps_errors_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(data_root=Path(folder))
            settings.ensure_directories()
            clock = lambda: datetime(2026, 11, 12, 20, tzinfo=timezone.utc)
            journal = DailyMainLog(settings, clock=clock)
            rows = [{"timestamp": clock().isoformat(), "local_date": "2026-11-12", "code": "task.exception", "level": "error", "data": {"unit_id": "q1"}},
                    {"timestamp": clock().isoformat(), "code": "assignment.heartbeat"}]
            journal.main.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            journal.prepare()
            original = journal.main.read_bytes()
            journal.prepare()
            self.assertEqual(journal.main.read_bytes(), original)
            self.assertEqual([row["code"] for row in iter_records(original.decode().splitlines(keepends=True))], ["task.exception"])
            self.assertEqual(json.loads(journal.state_path.read_text())["format_version"], 2)

    def test_bad_legacy_record_is_preserved_for_review(self):
        rows = list(iter_records(["broken legacy record\n"], preserve_invalid=True))
        self.assertEqual(rows[0]["code"], "log.unparsed")
        self.assertIn("broken legacy", rows[0]["message"])

    def test_heading_cannot_create_a_forged_log_event(self):
        raw = render_record({"code": "safe\nforged", "component": "test\rcomponent"})
        self.assertEqual(len(list(iter_records(raw.splitlines(keepends=True)))), 1)
        self.assertNotIn("\r", raw.splitlines()[0])
