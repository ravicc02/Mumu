from __future__ import annotations

import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.config import Settings
from app.db import Database


class AssignmentCollectionCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.db = Database(self.settings); self.db.initialize()
        self.account = self.db.create_account('fixture', 'chaoxing', self.settings)

    def tearDown(self):
        self.temp.cleanup()

    def assignment(self, work, *, account=None, year=2026, course='course', run=None):
        account = account or self.account
        return self.db.upsert_assignment(account['id'], 'chaoxing', {
            'academicYear': year, 'semester': f'{year}-{year+1}-1', 'courseId': course,
            'classId': 'class', 'workId': work, 'courseName': course, 'title': work,
            'status': 'unfinished', 'submittable': True, 'taskUrl': 'https://example.test/work',
            'assessment': {'policy_version': 2, 'execution_state': 'direct_fill', 'automation_supported': True},
        }, self.settings, run_id=run)

    def finish(self, run, status='success', *, count=1, failed=0, errors=None):
        return self.db.finish_collection_run(run['id'], status, total_courses=2, processed_courses=2-failed,
            failed_courses=failed, assignment_count=count, submittable_count=count, errors=errors)

    def cached(self, account=None, year=None):
        return self.db.list_assignments(account_id=(account or self.account)['id'], academic_year=year, cached=True)

    def test_previous_collection_survives_running_failure_and_database_restart(self):
        first = self.db.create_collection_run(self.account['id'], 2026)
        identifier = self.assignment('saved', run=first['id']); self.finish(first)
        newer = self.db.create_collection_run(self.account['id'], 2026)
        self.assignment('half-written', run=newer['id'])
        self.assertEqual([item['id'] for item in self.cached()], [identifier])
        self.finish(newer, 'failed', count=0, failed=2)
        restarted = Database(self.settings); restarted.initialize()
        self.assertEqual([item['id'] for item in restarted.list_assignments(account_id=self.account['id'], cached=True)], [identifier])
        self.assertEqual(len(self.db.list_assignments(self.account['id'])), 2)

    def test_success_replaces_snapshot_and_a_successful_empty_result_stays_empty(self):
        first = self.db.create_collection_run(self.account['id'], 2026)
        self.assignment('old', run=first['id']); self.finish(first)
        second = self.db.create_collection_run(self.account['id'], 2026)
        newer = self.assignment('new', run=second['id'])
        self.db.finalize_assignment_snapshot(self.account['id'], 2026, second['id'])
        self.assertEqual(self.cached()[0]['title'], 'old')
        self.finish(second)
        self.assertEqual([item['id'] for item in self.cached()], [newer])
        third = self.db.create_collection_run(self.account['id'], 2026)
        self.db.finalize_assignment_snapshot(self.account['id'], 2026, third['id'])
        self.finish(third, count=0)
        self.assertEqual(self.cached(), [])

    def test_partial_collection_retains_failed_courses_and_replaces_verified_courses(self):
        first = self.db.create_collection_run(self.account['id'], 2026)
        self.assignment('old-one', course='one', run=first['id'])
        retained = self.assignment('old-two', course='two', run=first['id']); self.finish(first, count=2)
        second = self.db.create_collection_run(self.account['id'], 2026)
        updated = self.assignment('new-one', course='one', run=second['id'])
        self.assignment('unconfirmed-two', course='two', run=second['id'])
        self.db.finalize_assignment_snapshot(self.account['id'], 2026, second['id'], failed_course_ids={'two'})
        self.finish(second, 'partial', count=1, failed=1, errors=[{'course_id':'two', 'error':'offline'}])
        self.assertCountEqual([item['id'] for item in self.cached()], [updated, retained])

    def test_accounts_years_and_inspection_results_remain_separate(self):
        other = self.db.create_account('fixture', 'chaoxing', self.settings)
        for account, year in [(self.account, 2025), (self.account, 2026), (other, 2026)]:
            run = self.db.create_collection_run(account['id'], year)
            self.assignment('same-name', account=account, year=year, course=f'course-{year}', run=run['id']); self.finish(run)
        current = self.cached(year=2026)[0]
        checked = {'policy_version': 2, 'execution_state': 'manual_required', 'reason': '需交报告'}
        self.db.update_assignment_assessment(current['id'], checked)
        self.assertEqual(self.cached(year=2026)[0]['assessment'], checked)
        self.assertTrue(self.cached(year=2025)[0]['assessment']['automation_supported'])
        self.assertTrue(self.cached(account=other)[0]['assessment']['automation_supported'])
        self.assertEqual(len(self.cached()), 2)

    def test_existing_records_are_seeded_before_the_first_new_collection(self):
        saved = self.assignment('legacy')
        self.assertEqual(self.cached()[0]['id'], saved)
        run = self.db.create_collection_run(self.account['id'], 2026)
        self.assignment('unconfirmed', run=run['id'])
        self.finish(run, 'failed', count=0, failed=2)
        self.assertEqual([item['id'] for item in self.cached()], [saved])

    def test_corrupt_cache_falls_back_without_breaking_reinspection(self):
        run = self.db.create_collection_run(self.account['id'], 2026)
        identifier = self.assignment('saved', run=run['id']); self.finish(run)
        with self.db.connect() as db:
            db.execute("UPDATE assignment_collection_cache SET items_json='not-json'")
        self.db.update_assignment_assessment(identifier, {'execution_state': 'manual_required'})
        self.assertEqual(self.cached()[0]['id'], identifier)

    def test_foreign_snapshot_cannot_leak_into_another_account(self):
        run = self.db.create_collection_run(self.account['id'], 2026)
        identifier = self.assignment('saved', run=run['id']); self.finish(run)
        item = self.cached()[0]; item['account_id'] = 'foreign'
        with self.db.connect() as db:
            db.execute('UPDATE assignment_collection_cache SET items_json=?', (json.dumps([item]),))
        self.assertEqual(self.cached()[0]['account_id'], self.account['id'])
        self.assertEqual(self.cached()[0]['id'], identifier)

    def test_list_api_reads_the_last_completed_snapshot_during_collection(self):
        from app import main
        first = self.db.create_collection_run(self.account['id'], 2026)
        saved = self.assignment('saved', run=first['id']); self.finish(first)
        current = self.db.create_collection_run(self.account['id'], 2026)
        self.assignment('not-complete', run=current['id'])

        async def verify():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                response = await client.get('/api/assignments', params={'account_id':self.account['id'], 'submittable':'false'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([item['id'] for item in response.json()], [saved])
                self.assertEqual((await client.get('/api/semesters', params={'account_id':self.account['id']})).json(), ['2026-2027-1'])
        with patch.object(main, 'database', self.db):
            asyncio.run(verify())

    def test_deleting_account_removes_its_collection_cache(self):
        run = self.db.create_collection_run(self.account['id'], 2026)
        self.assignment('saved', run=run['id']); self.finish(run)
        with self.db.connect() as db:
            db.execute('DELETE FROM accounts WHERE id=?', (self.account['id'],))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assignment_collection_cache').fetchone()[0], 0)

    def test_running_collection_is_marked_failed_after_restart(self):
        run = self.db.create_collection_run(self.account['id'], 2026)

        restarted = Database(self.settings); restarted.initialize()

        with restarted.connect() as db:
            row = db.execute('SELECT status,error FROM collection_runs WHERE id=?', (run['id'],)).fetchone()
        self.assertEqual(row['status'], 'failed')
        self.assertIn('采集已中断', row['error'])
