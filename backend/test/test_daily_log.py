from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.diagnostics.bridge import LegacyLogImporter
from app.diagnostics.daily import DailyMainLog
from app.diagnostics.layout import iter_records
from app.diagnostics.logging import DailyLogHandler


LOCAL = timezone(timedelta(hours=8))


def entries(path):
    try: return list(iter_records(path.read_text(encoding="utf-8").splitlines(keepends=True)))
    except FileNotFoundError: return []


class Clock:
    def __init__(self): self.value = datetime(2026, 11, 12, 23, 59, 58, tzinfo=LOCAL)
    def __call__(self): return self.value


class DailyLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name), openai_api_key=("sk-" + "fixture-" + "a" * 32))
        self.settings.ensure_directories()
        self.clock = Clock()
        self.journal = DailyMainLog(self.settings, clock=self.clock)

    def tearDown(self): self.temp.cleanup()

    def write(self, code, **kwargs): self.journal.write({"timestamp": self.clock().isoformat(), "code": code, **kwargs})

    def test_midnight_archives_all_previous_days_without_overwriting_existing_history(self):
        self.journal.archive.write_text(json.dumps({"code":"older-history"})+"\n", encoding="utf-8")
        self.write("day-one")
        self.clock.value += timedelta(seconds=3)
        self.journal.prepare()  # Idle day change requires no new business event.
        self.assertTrue(all(row["local_date"] == "2026-11-13" for row in entries(self.journal.main)))
        self.assertEqual([row["code"] for row in entries(self.journal.archive) if row["code"] != "log.day_started"], ["older-history", "day-one"])
        self.write("day-two")
        self.clock.value += timedelta(days=1)
        self.journal.prepare()
        codes = [row["code"] for row in entries(self.journal.archive)]
        self.assertEqual(codes.count("day-one"), 1)
        self.assertEqual(codes.count("day-two"), 1)
        self.assertTrue(all(row["local_date"] == "2026-11-14" for row in entries(self.journal.main)))

    def test_restart_on_same_day_preserves_main_and_restart_next_day_rolls_it(self):
        self.write("before-restart")
        original = self.journal.main.read_bytes()
        restarted = DailyMainLog(self.settings, clock=self.clock)
        restarted.prepare()
        self.assertEqual(restarted.main.read_bytes(), original)
        self.clock.value += timedelta(days=1)
        after = DailyMainLog(self.settings, clock=self.clock)
        after.prepare()
        self.assertTrue(any(row["code"] == "before-restart" for row in entries(after.archive)))
        self.assertFalse(any(row["code"] == "before-restart" for row in entries(after.main)))

    def test_local_date_is_used_for_utc_events_and_late_records_go_only_to_archive(self):
        self.journal.write({"timestamp":"2026-11-11T17:00:00+00:00","code":"local-today"})
        self.journal.write({"timestamp":"2026-11-11T15:00:00+00:00","code":"local-yesterday"})
        self.assertTrue(any(row["code"] == "local-today" for row in entries(self.journal.main)))
        self.assertFalse(any(row["code"] == "local-yesterday" for row in entries(self.journal.main)))
        self.assertTrue(any(row["code"] == "local-yesterday" for row in entries(self.journal.archive)))

    def test_same_day_size_does_not_create_parts_or_move_today_into_archive(self):
        for index in range(180): self.write("large-day", data={"index":index,"text":"x"*32000})
        self.assertGreater(self.journal.main.stat().st_size, 5*1024*1024)
        self.assertEqual(sum(row["code"]=="large-day" for row in entries(self.journal.main)), 180)
        self.assertEqual(self.journal.archive.stat().st_size, 0)
        self.assertFalse(list(self.settings.logs_root.glob("main.log.*")))

    def test_each_day_repeats_active_task_context_before_new_steps(self):
        context={"environment":{"app_version":"fixture"},"task":{"id":"task-one"}}
        self.journal.write({"timestamp":self.clock().isoformat(),"trace_id":"task-one","code":"step-one"},context=context)
        self.clock.value += timedelta(days=1)
        self.journal.write({"timestamp":self.clock().isoformat(),"trace_id":"task-one","code":"step-two"},context=context)
        rows=entries(self.journal.main)
        self.assertEqual([row["code"] for row in rows], ["log.day_started","task.context","step-two"])
        self.assertEqual(rows[1]["data"]["environment"]["app_version"],"fixture")

    def test_partial_archive_append_recovers_without_duplicating_or_losing_old_entries(self):
        self.write("previous")
        self.clock.value += timedelta(days=1)
        with self.journal.locked():
            state=self.journal._state()
            state["pending"]={"archive_offset":0,"target_day":self.clock().date().isoformat()}
            self.journal._save(state)
            os.replace(self.journal.main,self.journal.staged)
            self.journal.archive.write_bytes(b"partial archive output")
        recovered=DailyMainLog(self.settings,clock=self.clock)
        recovered.prepare()
        self.assertEqual(sum(row["code"]=="previous" for row in entries(recovered.archive)),1)
        self.assertFalse(recovered.staged.exists())
        self.assertTrue(all(row["local_date"]=="2026-11-13" for row in entries(recovered.main)))

    def test_unconfirmed_staging_file_is_preserved_if_state_is_corrupt(self):
        self.journal.control.mkdir(parents=True)
        self.journal.staged.write_text("important staged log",encoding="utf-8")
        self.journal.state_path.write_text("{}",encoding="utf-8")
        with self.assertRaises(ValueError): self.journal.prepare()
        self.assertTrue(self.journal.staged.exists())

    def test_invalid_day_state_recovers_date_from_main_header(self):
        self.write("keep-today")
        state=json.loads(self.journal.state_path.read_text(encoding="utf-8"))
        state["active_day"]="invalid-date"
        self.journal.state_path.write_text(json.dumps(state),encoding="utf-8")
        self.journal.prepare()
        self.assertTrue(any(row["code"]=="keep-today" for row in entries(self.journal.main)))
        self.assertEqual(self.journal.archive.stat().st_size,0)

    def test_thread_writers_keep_complete_lines_from_multiple_instances(self):
        def write_batch(index):
            writer=DailyMainLog(self.settings,clock=self.clock)
            for number in range(25): writer.write({"timestamp":self.clock().isoformat(),"code":"parallel","message":f"{index}:{number}"})
        with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(write_batch,range(4)))
        rows=[row for row in entries(self.journal.main) if row["code"]=="parallel"]
        self.assertEqual(len(rows),100)
        self.assertEqual(len({row["message"] for row in rows}),100)

    def test_cross_process_writers_share_the_same_file_lock(self):
        script="""import sys
from pathlib import Path
from app.config import Settings
from app.diagnostics.daily import DailyMainLog
j=DailyMainLog(Settings(data_root=Path(sys.argv[1])))
for n in range(20): j.write({'code':'process','message':sys.argv[2]+':'+str(n)})
"""
        processes=[subprocess.Popen([sys.executable,"-c",script,self.temp.name,str(index)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for index in range(2)]
        for process in processes:
            output,error=process.communicate(timeout=30)
            self.assertEqual(process.returncode,0,error.decode(errors="replace"))
        rows=[row for row in entries(self.journal.main) if row["code"]=="process"]
        self.assertEqual(len(rows),40)
        self.assertEqual(len({row["message"] for row in rows}),40)

    def test_credentials_are_scrubbed_and_storage_failure_does_not_print_the_raw_message(self):
        self.write("secret-check",data={"api_key":self.settings.openai_api_key,"Cookie":"private-cookie","Authorization":"Bearer private-bearer"})
        raw=self.journal.main.read_text(encoding="utf-8")
        for secret in (self.settings.openai_api_key,"private-cookie","private-bearer"): self.assertNotIn(secret,raw)
        handler=DailyLogHandler(self.settings)
        record=logging.LogRecord("fixture",logging.ERROR,__file__,1,"secret "+self.settings.openai_api_key,(),None)
        captured=io.StringIO()
        try:
            with patch.object(handler.journal,"write",side_effect=OSError("disk full")),redirect_stderr(captured): handler.emit(record)
        finally: handler.close()
        self.assertIn("OSError",captured.getvalue())
        self.assertNotIn(self.settings.openai_api_key,captured.getvalue())

    def test_idle_maintenance_rotates_at_local_midnight_without_new_events(self):
        handler=DailyLogHandler(self.settings)
        handler.journal.clock=self.clock
        self.clock.value=datetime(2026,11,12,23,59,59,900000,tzinfo=LOCAL)
        handler.journal.write({"timestamp":self.clock().isoformat(),"code":"idle-before-midnight"})
        handler.worker=threading.Thread(target=handler._maintain,daemon=True)
        handler.worker.start()
        self.clock.value += timedelta(seconds=1)
        try:
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                with handler.journal.locked(): rows=entries(handler.journal.main)
                if rows and all(row["local_date"]=="2026-11-13" for row in rows): break
                time.sleep(.03)
            rows=entries(handler.journal.main)
            self.assertTrue(rows)
            self.assertTrue(all(row["local_date"]=="2026-11-13" for row in rows))
            self.assertTrue(any(row.get("code")=="idle-before-midnight" for row in entries(handler.journal.archive)))
        finally: handler.close()


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.settings=Settings(data_root=Path(self.temp.name),openai_api_key="sk-fixture-log-key-123456789")
        self.settings.ensure_directories()
        self.clock=Clock(); self.journal=DailyMainLog(self.settings,clock=self.clock)

    def tearDown(self): self.temp.cleanup()

    def test_existing_today_and_old_logs_are_split_and_restarts_do_not_reimport(self):
        rows=[{"timestamp":"2026-11-11T20:00:00+08:00","code":"yesterday"},{"timestamp":"2026-11-12T20:00:00+08:00","code":"today","api_key":self.settings.openai_api_key}]
        legacy=self.settings.logs_root/"backend.jsonl"
        legacy.write_text("\n".join(json.dumps(row) for row in rows)+"\n",encoding="utf-8")
        importer=LegacyLogImporter(self.settings,self.journal)
        self.assertEqual(importer.poll(final=True),2)
        self.assertEqual(LegacyLogImporter(self.settings,self.journal).poll(final=True),0)
        self.assertTrue(any(row["code"]=="today" for row in entries(self.journal.main)))
        self.assertFalse(any(row["code"]=="yesterday" for row in entries(self.journal.main)))
        self.assertTrue(any(row["code"]=="yesterday" for row in entries(self.journal.archive)))
        self.assertNotIn(self.settings.openai_api_key,self.journal.main.read_text(encoding="utf-8"))
        self.assertTrue(legacy.exists())

    def test_partial_legacy_line_waits_until_finished(self):
        legacy=self.settings.logs_root/"backend.jsonl"
        legacy.write_bytes(b'{"timestamp":"2026-11-12T20:00:00+08:00","code":')
        importer=LegacyLogImporter(self.settings,self.journal)
        self.assertEqual(importer.poll(),0)
        with legacy.open("ab") as output: output.write(b'"completed-line"}\n')
        self.assertEqual(importer.poll(),1)
        self.assertEqual(sum(row["code"]=="completed-line" for row in entries(self.journal.main)),1)

    def test_explicit_launcher_stream_can_be_imported_without_scanning_other_files(self):
        with tempfile.TemporaryDirectory() as streams:
            stream=Path(streams)/"stderr.log"
            stream.write_text(json.dumps({"timestamp":"2026-11-12T20:00:00+08:00","message":"startup detail"})+"\n",encoding="utf-8")
            (Path(streams)/"unrelated.log").write_text("unrelated private file",encoding="utf-8")
            importer=LegacyLogImporter(self.settings,self.journal,extra_sources=[stream])
            self.assertEqual(importer.poll(final=True),1)
            raw=self.journal.main.read_text(encoding="utf-8")
            self.assertIn("startup detail",raw)
            self.assertIn("launcher/stderr.log",raw)
            self.assertNotIn("unrelated private file",raw)

    def test_new_trace_events_are_not_imported_again_and_old_controls_are_self_contained(self):
        folder=self.settings.logs_root/"diagnostics/v1/assignment/trace-one"
        evidence=folder/"evidence"; evidence.mkdir(parents=True)
        (folder/"manifest.json").write_text(json.dumps({"main_log_started_at":"2026-11-12T12:00:00+08:00"}),encoding="utf-8")
        old={"timestamp":"2026-11-12T11:00:00+08:00","trace_id":"trace-one","code":"before"}
        new={"timestamp":"2026-11-12T13:00:00+08:00","trace_id":"trace-one","code":"after"}
        (folder/"events.jsonl").write_text(json.dumps(old)+"\n"+json.dumps(new)+"\n",encoding="utf-8")
        (evidence/"controls.json").write_text(json.dumps({"captured_at":"2026-11-12T11:00:00+08:00","data":{"class":"check_answer_dx","unit_id":"q22:r2"}}),encoding="utf-8")
        self.journal.write(new)
        LegacyLogImporter(self.settings,self.journal).poll(final=True)
        rows=entries(self.journal.main)
        self.assertEqual(sum(row["code"]=="after" for row in rows),1)
        self.assertEqual(sum(row["code"]=="before" for row in rows),1)
        snapshot=next(row for row in rows if row["code"]=="diagnostics.evidence")
        self.assertEqual(snapshot["trace_id"],"trace-one")
        self.assertEqual(snapshot["task_kind"],"assignment")
        self.assertIn("check_answer_dx",json.dumps(snapshot))

    def test_legacy_import_scrubs_database_model_keys_and_known_cookie_values(self):
        from app.db import Database
        database=Database(self.settings); database.initialize()
        database.set_setting("model_config",{"api_key":"private-database-model-secret"})
        account=database.create_account("fixture account","chaoxing",self.settings)
        Path(account["storage_state_path"]).write_text(json.dumps({"cookies":[{"name":"session","value":"private-known-cookie-value"}],"origins":[]}),encoding="utf-8")
        row={"timestamp":"2026-11-12T20:00:00+08:00","message":"upstream failure private-database-model-secret private-known-cookie-value"}
        (self.settings.logs_root/"backend.jsonl").write_text(json.dumps(row)+"\n",encoding="utf-8")
        LegacyLogImporter(self.settings,self.journal).poll(final=True)
        raw=self.journal.main.read_text(encoding="utf-8")
        self.assertNotIn("private-database-model-secret",raw)
        self.assertNotIn("private-known-cookie-value",raw)

    def test_legacy_long_task_repeats_its_environment_in_the_new_day_main_file(self):
        folder=self.settings.logs_root/"diagnostics/v1/learning/long-task"
        folder.mkdir(parents=True)
        (folder/"manifest.json").write_text(json.dumps({"task_kind":"learning","environment":{"app_version":"legacy-fixture"}}),encoding="utf-8")
        source=folder/"task.log"
        source.write_text(json.dumps({"timestamp":self.clock().isoformat(),"trace_id":"long-task","code":"first"})+"\n",encoding="utf-8")
        importer=LegacyLogImporter(self.settings,self.journal)
        importer.poll(final=True)
        self.clock.value += timedelta(days=1)
        with source.open("a",encoding="utf-8") as output: output.write(json.dumps({"timestamp":self.clock().isoformat(),"trace_id":"long-task","code":"second"})+"\n")
        importer.poll(final=True)
        rows=entries(self.journal.main)
        self.assertEqual([row["code"] for row in rows],["log.day_started","task.context","second"])
        self.assertEqual(rows[1]["data"]["environment"]["app_version"],"legacy-fixture")
        self.assertEqual(rows[2]["task_kind"],"learning")


class DailyRuntimeTests(unittest.TestCase):
    def test_backend_bootstrap_writes_startup_and_access_to_main_not_backend_jsonl(self):
        with tempfile.TemporaryDirectory() as folder:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1",0)); port=listener.getsockname()[1]
            environment={**os.environ,"MUMU_DATA":folder}
            server_script=Path(__file__).resolve().parents[1]/"run_server.py"
            process=subprocess.Popen([sys.executable,str(server_script),"--host","127.0.0.1","--port",str(port)],
                env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            try:
                deadline=time.monotonic()+25
                healthy=False
                while time.monotonic()<deadline and process.poll() is None:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health",timeout=.5) as response:
                            healthy=json.load(response)["ok"]
                        break
                    except OSError: time.sleep(.1)
                self.assertTrue(healthy,"isolated backend did not become healthy")
                main=Path(folder)/"logs/main.log"
                rows=entries(main)
                self.assertTrue(any(row.get("code")=="app.context" for row in rows))
                self.assertTrue(any("started" in row.get("message","").lower() for row in rows))
                self.assertFalse((Path(folder)/"logs/backend.jsonl").exists())
            finally:
                if process.poll() is None: process.terminate()
                process.communicate(timeout=10)

    def test_compatibility_bridge_follows_an_old_writer_without_stopping_it(self):
        with tempfile.TemporaryDirectory() as folder:
            script="""import json,sys,time
from pathlib import Path
from datetime import datetime,timezone
p=Path(sys.argv[1])/'logs/backend.jsonl';p.parent.mkdir(parents=True,exist_ok=True)
with p.open('a',encoding='utf-8') as f:f.write(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'code':'live-first'})+'\\n')
time.sleep(2)
with p.open('a',encoding='utf-8') as f:f.write(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'code':'live-second'})+'\\n')
time.sleep(2)
"""
            flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
            writer=subprocess.Popen([sys.executable,"-c",script,folder],creationflags=flags)
            bridge=subprocess.Popen([sys.executable,"-m","app.diagnostics.bridge","--data-root",folder,"--follow-pid",str(writer.pid)],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=flags)
            try:
                main=Path(folder)/"logs/main.log"
                deadline=time.monotonic()+3
                first=False
                while time.monotonic()<deadline:
                    if any(row.get("code")=="live-first" for row in entries(main)):
                        first=True; break
                    time.sleep(.05)
                self.assertTrue(first)
                self.assertIsNone(writer.poll(),"the bridge must not stop its source backend")
                self.assertEqual(writer.wait(timeout=10),0)
                output,error=bridge.communicate(timeout=10)
                self.assertEqual(bridge.returncode,0,error.decode(errors="replace"))
                codes=[row["code"] for row in entries(main)]
                self.assertEqual(codes.count("live-first"),1)
                self.assertEqual(codes.count("live-second"),1)
            finally:
                for process in (writer,bridge):
                    if process.poll() is None: process.terminate()
                    process.wait(timeout=10)
