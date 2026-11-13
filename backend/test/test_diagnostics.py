from __future__ import annotations

import hashlib
from contextlib import nullcontext
import io
import json
import logging
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright

from app.config import Settings
from app.db import Database
from app.diagnostics.bundle import build_bundle
from app.diagnostics.logging import DiagnosticFormatter, TaskLogHandler, current_trace
from app.diagnostics.layout import iter_records
from app.diagnostics.privacy import Scrubber, task_scrubber
from app.diagnostics.probe import capture_page
from app.diagnostics.recorder import DiagnosticStore, NullTrace
from app.diagnostics.routes import diagnostic_router, install_request_diagnostics
from app.services.runtime.browser import chrome_executable


def setup(root, *, legacy=False):
    settings = Settings(data_root=Path(root), openai_api_key=("sk-" + "fixture-" + "a" * 32), openai_model="fixture-model")
    settings.ensure_directories()
    db = Database(settings); db.initialize()
    account = db.create_account("RemoteTester", "chaoxing", settings)
    Path(account['storage_state_path']).write_text(json.dumps({'cookies': [{'name':'session', 'value':'private-cookie-12345678', 'domain':'.example.test', 'expires':1234567890, 'httpOnly':True}],
        'origins':[{'origin':'https://example.test', 'localStorage':[{'name':'auth', 'value':'private-localstorage-12345678'}]}]}), encoding='utf-8')
    assignment = db.upsert_assignment(account['id'], 'chaoxing', {'title':'Private course title', 'taskUrl':'https://example.test/work?enc=secret-url&cpi=12345',
        'workId':'work','courseId':'course','classId':'class','submittable':True}, settings)
    with patch.object(db.diagnostics, 'start', return_value=NullTrace()) if legacy else nullcontext():
        job = db.create_job(account['id'], assignment, False, True)
    return settings, db, account, job


def unpack(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive: return {name:archive.read(name) for name in archive.namelist()}


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings, self.db, self.account, self.job = setup(self.temp.name)
        self.trace = self.db.diagnostics.trace('assignment', self.job['id'])

    def tearDown(self): self.temp.cleanup()

    def test_single_task_log_contains_context_evidence_errors_and_result(self):
        self.trace.snapshot('controls', {'unit_id': 'q22:r2', 'class': 'check_answer_dx',
            'question': 'fixture question', 'cookie': 'private-cookie-12345678'})
        try:
            raise ValueError('control mismatch private-cookie-12345678')
        except ValueError as error:
            self.trace.exception(error, phase='verification')
        self.trace.summary({**self.job, 'status': 'incomplete'})
        raw = (self.settings.logs_root / 'main.log').read_text(encoding='utf-8')
        records = list(iter_records(raw.splitlines(keepends=True)))
        context = next(row for row in records if row.get('code') == 'task.context')
        self.assertIn('dependencies', context['data']['environment'])
        self.assertTrue(all(row['trace_id'] == self.job['id'] for row in records if row.get('trace_id')))
        self.assertIn('fixture question', raw)
        self.assertIn('check_answer_dx', raw)
        self.assertIn('q22:r2', raw)
        self.assertNotIn('private-cookie-12345678', raw)
        self.assertNotIn(self.settings.openai_api_key, raw)
        failure = next(row for row in records if row['code'] == 'task.exception')
        self.assertEqual(failure['phase'], 'verification')
        self.assertIn('ValueError', failure['data']['stack'])
        self.assertEqual(records[-1]['data']['status'], 'incomplete')

    def test_task_log_captures_only_runtime_logs_for_its_context(self):
        handler = TaskLogHandler()
        token = current_trace.set(self.trace)
        try:
            handler.handle(logging.LogRecord('player', logging.WARNING, __file__, 1,
                'media interrupted Bearer secret-bearer-token', (), None))
        finally: current_trace.reset(token)
        handler.handle(logging.LogRecord('other', logging.ERROR, __file__, 1, 'unrelated task', (), None))
        records = list(iter_records((self.settings.logs_root / 'main.log').read_text(encoding='utf-8').splitlines(keepends=True)))
        runtime = next(row for row in records if row['code'] == 'runtime.log')
        self.assertEqual(runtime['component'], 'player')
        self.assertEqual(runtime['level'], 'warning')
        self.assertNotIn('secret-bearer-token', runtime['message'])
        self.assertNotIn('unrelated task', str(records))

    def test_task_details_share_the_daily_main_file_without_task_log_fragments(self):
        for index in range(8): self.trace.record('progress', data={'index': index})
        records = list(iter_records((self.settings.logs_root / 'main.log').read_text(encoding='utf-8').splitlines(keepends=True)))
        self.assertEqual([row['data']['index'] for row in records if row.get('code') == 'progress'], list(range(8)))
        self.assertFalse(list(self.trace.folder.glob('task*.log')))
        self.assertEqual((self.settings.logs_root / 'oldmain.log').read_text(encoding='utf-8'), '')

    def test_task_log_failure_does_not_break_task_event(self):
        with patch.object(self.trace, '_append_task_log', side_effect=OSError('disk full')):
            self.db.add_event(self.job['id'], 'progress', 'business event survives')
        self.assertTrue(self.trace.failed)
        self.assertEqual(self.db.list_events(self.job['id'])[-1]['message'], 'business event survives')

    def test_task_creation_and_controls_are_automatically_journaled_with_correlation(self):
        self.db.add_event(self.job['id'], 'paused', 'Manual pause', {'unit_id':'q28:r1','step':4})
        lines = [json.loads(line) for line in (self.trace.folder/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(lines[0]['code'], 'task.created')
        self.assertEqual(lines[-1]['code'], 'assignment.paused')
        self.assertEqual(lines[-1]['trace_id'], self.job['id'])
        self.assertEqual(lines[-1]['source_event_id'], self.db.list_events(self.job['id'])[0]['id'])
        self.assertEqual(lines[-1]['timestamp'][-6:], '+00:00')
        self.assertEqual([item['sequence'] for item in lines], sorted(set(item['sequence'] for item in lines)))
        self.assertEqual(self.trace.manifest['environment']['dependencies']['playwright'].count('.') >= 1, True)

    def test_credentials_are_redacted_at_storage_and_export_even_when_content_selected(self):
        self.assertNotIn('tiny', task_scrubber(Settings(data_root=self.settings.data_root,openai_api_key='tiny')).text('provider rejected tiny'))
        value = {'api_key':self.settings.openai_api_key, 'headers':{'Authorization':'Bearer unknown-secret', 'Cookie':'sid=unknown-cookie'},
            'data':json.dumps({'enc':'json-payload-enc-secret','cpi':'private-json-person','objectid':'resource-one'}),
            'url':'https://name:pass@example.test/work?enc=private-query&cpi=private-person#token', 'value':'private-cookie-12345678',
            'input_text':'private-localstorage-12345678', 'account_name':self.account['name']}
        self.db.add_event(self.job['id'], 'error', 'failed for RemoteTester at https://example.test/work?enc=private-query', value)
        self.trace.snapshot('inputs', value, content=True)
        files = unpack(build_bundle(self.db, 'assignment', self.job['id'], include_content=True))
        all_text = '\n'.join(item.decode('utf-8') for item in files.values())
        for secret in ('private-query','private-person','private-cookie-12345678','private-localstorage-12345678', self.settings.openai_api_key, 'unknown-secret','unknown-cookie','RemoteTester','name:pass','json-payload-enc-secret','private-json-person'):
            self.assertNotIn(secret, all_text)
        raw = (self.trace.folder/'events.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('private-cookie-12345678', raw)
        stored = json.dumps(self.db.list_events(self.job['id']))
        self.assertNotIn('private-query', stored)

    def test_content_selection_is_explicit_and_all_exported_files_have_correct_checksums(self):
        self.trace.snapshot('questions', {'question':'UNIQUE PRIVATE QUESTION','answers':['UNIQUE PRIVATE ANSWER']}, content=True)
        self.trace.snapshot('page', {'dom':[{'tag':'div','attributes':{'class':'check_answer_dx','aria-label':'UNIQUE PRIVATE LABEL'}, 'children':[{'tag':'#text','text':'UNIQUE PRIVATE TEXT'}]}]})
        basic = unpack(build_bundle(self.db, 'assignment', self.job['id']))
        text = '\n'.join(value.decode('utf-8') for value in basic.values())
        for content in ('UNIQUE PRIVATE QUESTION','UNIQUE PRIVATE ANSWER','UNIQUE PRIVATE LABEL','UNIQUE PRIVATE TEXT'): self.assertNotIn(content, text)
        self.assertIn('check_answer_dx', text)
        full = unpack(build_bundle(self.db, 'assignment', self.job['id'], include_content=True))
        self.assertIn('UNIQUE PRIVATE QUESTION', '\n'.join(value.decode() for value in full.values()))
        manifest = json.loads(full['manifest.json'])
        for entry in manifest['files']:
            self.assertEqual(entry['bytes'], len(full[entry['path']]))
            self.assertEqual(entry['sha256'], hashlib.sha256(full[entry['path']]).hexdigest())

    def test_runtime_export_is_scoped_to_the_selected_task(self):
        rows = [{'trace_id':self.job['id'],'message':'my error private-cookie-12345678'}, {'trace_id':'another-task','message':'OTHER ACCOUNT PRIVATE DATA'}]
        (self.settings.logs_root/'backend.jsonl').write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
        files = unpack(build_bundle(self.db,'assignment',self.job['id'],include_content=True))
        runtime = files['runtime.jsonl'].decode()
        self.assertIn('my error',runtime)
        self.assertNotIn('OTHER ACCOUNT PRIVATE DATA','\n'.join(value.decode() for value in files.values()))
        self.assertNotIn('private-cookie-12345678',runtime)

    def test_identity_redaction_does_not_corrupt_model_or_dom_class_names(self):
        scrubber = Scrubber(identities=['flash'])
        actual = scrubber.clean({'model':'deepseek-flash','class':'flash-card','message':'account flash failed'})
        self.assertEqual(actual['model'],'deepseek-flash')
        self.assertEqual(actual['class'],'flash-card')
        self.assertNotIn('flash',actual['message'])

    def test_relative_http_and_websocket_queries_do_not_leak_session_tickets(self):
        safe=Scrubber().text('WebSocket /api/learning/runs/example/browser?token=private-session-ticket&view=123 [accepted]')
        self.assertNotIn('private-session-ticket',safe)
        self.assertNotIn('view=123',safe)
        self.assertIn('/api/learning/runs/example/browser?',safe)
        self.assertIn('token=',safe)

    def test_basic_export_preserves_error_details_but_removes_known_question_text(self):
        self.trace.snapshot('questions', {'question':'UNIQUE QUESTION TEXT','answers':['UNIQUE ANSWER TEXT']}, content=True)
        self.trace.record('task.exception','Selector mismatch for UNIQUE QUESTION TEXT; expected check_answer_dx',{'exception_type':'RuntimeError','frames':[{'file':'adapter.py','line':19}]},level='error')
        files = unpack(build_bundle(self.db,'assignment',self.job['id']))
        report = files['REPORT.md'].decode()
        self.assertIn('Selector mismatch',report); self.assertIn('check_answer_dx',report)
        self.assertNotIn('UNIQUE QUESTION TEXT',report)
        scrubber = Scrubber(); scrubber.exclude_content_strings([{'answers':['dead','Windows']}])
        safe = scrubber.clean({'sha256':'dead'*16,'os':'Windows'},include_content=False)
        self.assertEqual(safe,{'sha256':'dead'*16,'os':'Windows'})

    def test_restart_updates_future_trace_state_without_fabricating_legacy_material(self):
        self.db.transition_job(self.job['id'],'running')
        self.db.mark_interrupted_jobs()
        manifest = json.loads((self.trace.folder/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['state'],'interrupted')
        self.assertIn('task.recovered',(self.trace.folder/'events.jsonl').read_text(encoding='utf-8'))

    def test_screenshot_export_rejects_files_outside_the_artifact_directory(self):
        outside = self.settings.data_root/'private.png'; outside.write_bytes(b'\x89PNG\r\n\x1a\nPRIVATE OUTSIDE ARTIFACT')
        self.db.add_artifact(self.job['id'],'screenshot',outside)
        files = unpack(build_bundle(self.db,'assignment',self.job['id'],include_screenshots=True))
        self.assertFalse(any(name.startswith('screenshots/') for name in files))
        self.assertTrue(any(item['reason']=='artifact_path_rejected' for item in json.loads(files['manifest.json'])['omissions']))

    def test_error_cause_request_identity_stack_and_span_are_retained(self):
        try:
            try:
                error = RuntimeError('upstream failed')
                error.response = SimpleNamespace(status_code=503, headers={'x-request-id':'request-remote-123'})
                raise error
            except RuntimeError as error: raise ValueError('model generation failed') from error
        except ValueError as error:
            self.trace.exception(error, phase='model_generation')
        with self.trace.span('model.request', {'unit_count':48}): pass
        records = [json.loads(line) for line in (self.trace.folder/'events.jsonl').read_text().splitlines()]
        exception = next(item for item in records if item['code']=='task.exception')
        self.assertEqual(exception['data']['http_status'],503)
        self.assertEqual(exception['data']['request_id'],'request-remote-123')
        self.assertEqual(len(exception['data']['causes']),2)
        self.assertTrue(exception['data']['frames'])
        self.assertEqual(records[-1]['data']['span_id'],records[-2]['data']['span_id'])
        self.assertGreaterEqual(records[-1]['data']['duration_ms'],0)

    def test_reopened_journal_recovers_sequence_and_reports_a_partial_tail(self):
        self.trace.record('one')
        path = self.trace.folder/'events.jsonl'
        with path.open('ab') as output: output.write(b'{"sequence":')
        manifest = json.loads((self.trace.folder/'manifest.json').read_text(encoding='utf-8')); manifest['last_sequence']=0
        (self.trace.folder/'manifest.json').write_text(json.dumps(manifest))
        reopened = DiagnosticStore(self.settings).trace('assignment',self.job['id'])
        reopened.record('after-restart')
        self.assertGreater(reopened.sequence,2)
        self.db.diagnostics.traces[('assignment',self.job['id'])] = reopened
        package = unpack(build_bundle(self.db,'assignment',self.job['id']))
        self.assertTrue(json.loads(package['manifest.json'])['read_errors'])

    def test_rotation_is_bounded_and_distinct_snapshots_do_not_overwrite_each_other(self):
        with patch('app.diagnostics.recorder.ROTATE_BYTES',800):
            for index in range(14): self.trace.record('row', data={'index':index,'long':'x'*100})
        self.assertLessEqual(len(list(self.trace.folder.glob('events*.jsonl'))),3)
        self.assertTrue(self.trace.manifest['events_rotated'])
        self.trace.snapshot('page', {'number':1}); self.trace.snapshot('page', {'number':2})
        self.assertEqual(len(list((self.trace.folder/'evidence').glob('*-page.json'))),2)

    def test_storage_failure_does_not_abort_the_business_event(self):
        with patch.object(self.trace,'_write_json',side_effect=OSError('disk full')):
            self.db.add_event(self.job['id'],'progress','still running',{'step':1})
        self.assertTrue(self.trace.failed)
        self.assertEqual(self.db.list_events(self.job['id'])[-1]['message'],'still running')

    def test_large_future_ui_events_are_bounded_and_keep_a_diagnostic_pointer(self):
        self.db.add_event(self.job['id'],'step','large output',{'step':7,'values':['large text '*3000]*4})
        event = self.db.list_events(self.job['id'])[-1]
        self.assertEqual(event['data']['omitted'],'event_size_limit')
        self.assertEqual(event['data']['step'],7)
        self.assertLess(len(json.dumps(event).encode()),65536)
        self.assertTrue(list((self.trace.folder/'evidence').glob('*-oversized-ui-event.json')))

    def test_expired_finished_logs_are_cleaned_but_live_and_retained_sessions_are_protected(self):
        self.trace.summary({**self.job,'status':'stopped'})
        live = self.db.diagnostics.start('learning','active-run',{'status':'running'},self.account)
        retained = self.db.diagnostics.start('learning','retained-run',{'status':'incomplete','browser_retained':1},self.account)
        retained.summary({'status':'incomplete','browser_retained':1})
        self.db.diagnostics.prune(days=0,total_bytes=1)
        self.assertFalse(self.trace.folder.exists())
        self.assertTrue(live.folder.exists()); self.assertTrue(retained.folder.exists())
        with self.assertRaises(ValueError): self.db.diagnostics.folder('assignment','../../outside')

    def test_runtime_formatter_correlates_task_and_scrubs_credentials(self):
        token = current_trace.set(self.trace)
        try:
            record = logging.LogRecord('browser', logging.ERROR, __file__, 1, 'failed Bearer leaked-header-value private-cookie-12345678', (), None)
            value = json.loads(DiagnosticFormatter(self.settings).format(record))
            self.assertEqual(value['trace_id'],self.job['id'])
            self.assertNotIn('leaked-header-value',value['message']); self.assertNotIn('private-cookie-12345678',value['message'])
        finally: current_trace.reset(token)

    def test_legacy_tasks_do_not_get_a_fabricated_capture(self):
        with tempfile.TemporaryDirectory() as root:
            _, db, _, job = setup(root,legacy=True)
            db.add_event(job['id'],'incomplete','old failure')
            files = unpack(build_bundle(db,'assignment',job['id']))
            manifest = json.loads(files['manifest.json'])
            self.assertFalse(manifest['capture_available'])
            self.assertEqual(manifest['omissions'][0]['reason'],'capture_missing_or_expired')
            self.assertFalse(db.diagnostics.folder('assignment',job['id']).exists())

    def test_corrupt_ui_event_json_does_not_prevent_export_of_the_structured_journal(self):
        self.db.add_event(self.job['id'],'error','An error happened')
        with self.db.connect() as connection: connection.execute('UPDATE job_events SET data_json=? WHERE job_id=?',('{broken',self.job['id']))
        files = unpack(build_bundle(self.db,'assignment',self.job['id']))
        self.assertIn('assignment.error',files['events.jsonl'].decode())
        self.assertTrue(any(item['reason']=='database_event_read_failed' for item in json.loads(files['manifest.json'])['read_errors']))


class DiagnosticAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_task_controls_keep_the_reason_and_request_correlation(self):
        with tempfile.TemporaryDirectory() as root:
            _, db, _, job = setup(root)
            app = FastAPI(); install_request_diagnostics(app,lambda:db)
            @app.post('/api/jobs/{identifier}/pause')
            async def pause(identifier): raise HTTPException(409,'Task state does not permit pause')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response = await client.post(f"/api/jobs/{job['id']}/pause")
                self.assertEqual(response.status_code,409)
                self.assertEqual(response.json()['detail'],'Task state does not permit pause')
                trace = db.diagnostics.trace('assignment',job['id'])
                records = [json.loads(row) for row in (trace.folder/'events.jsonl').read_text(encoding='utf-8').splitlines()]
                failure = next(item for item in records if item['code']=='task.exception')
                self.assertIn('Task state does not permit pause',failure['message'])
                self.assertEqual(failure['data']['api_request_id'],response.headers['x-diagnostic-request-id'])

    async def test_export_api_returns_a_zip_and_rejects_unknown_tasks_and_extra_fields(self):
        with tempfile.TemporaryDirectory() as root:
            _, db, _, job = setup(root)
            app = FastAPI(); app.include_router(diagnostic_router(lambda:db))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response = await client.get(f"/api/diagnostics/assignment/{job['id']}")
                self.assertTrue(response.json()['capture_available'])
                response = await client.post(f"/api/diagnostics/assignment/{job['id']}/export",json={'include_content':True})
                self.assertEqual(response.status_code,200,response.text if response.status_code!=200 else '')
                self.assertIn('REPORT.md',unpack(response.content))
                self.assertEqual(json.loads(unpack(response.content)['manifest.json'])['trace_id'],job['id'])
                self.assertEqual(response.headers['cache-control'],'no-store')
                self.assertEqual((await client.post('/api/diagnostics/assignment/missing/export',json={})).status_code,404)
                self.assertEqual((await client.post(f"/api/diagnostics/assignment/{job['id']}/export",json={'cookie':'unsafe'})).status_code,422)

    async def test_real_page_structure_and_offline_fixture_preserve_the_unseen_control_variant(self):
        with tempfile.TemporaryDirectory() as root:
            _, db, _, job = setup(root)
            trace = db.diagnostics.trace('assignment',job['id'])
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True,executable_path=str(chrome_executable()))
                try:
                    page = await browser.new_page()
                    await page.set_content('''<div class="questionLi" id="question28"><h3><span class="colorShallow">多选题</span>PRIVATE QUESTION TEXT</h3><div class="reading_answer" data-mumu-unit="q28:r1"><span class="read_type">多选题</span><span data="A" class="num_option_dx check_answer_dx" onclick="choose(this)">PRIVATE OPTION</span><input type="password" name="password" value="secret-password-not-captured"></div></div>''')
                    await capture_page(trace,page,'failure-controls',['q28:r1'])
                    files = unpack(build_bundle(db,'assignment',job['id']))
                    fixture_name = next(name for name in files if name.startswith('replay/'))
                    markup = files[fixture_name].decode()
                    self.assertIn('check_answer_dx',markup)
                    self.assertNotIn('PRIVATE OPTION',markup)
                    self.assertNotIn('secret-password-not-captured','\n'.join(value.decode() for value in files.values()))
                    self.assertNotIn('onclick=',markup); self.assertNotIn('<script',markup)
                    await page.set_content(markup)
                    self.assertEqual(await page.locator('.check_answer_dx').count(),1)
                finally: await browser.close()
