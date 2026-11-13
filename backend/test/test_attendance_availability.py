import time
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.db import Database, utc_now
from app.services.attendance.availability import timestamp, window
from app.services.attendance.client import AttendanceClient
from app.services.attendance.store import AttendanceStore
from app.services.attendance.contracts import AttendanceError

NOW = datetime(2026,11,13,12,tzinfo=timezone(timedelta(hours=8))).timestamp()


class AttendanceWindowTests(unittest.TestCase):
    def test_seconds_milliseconds_date_objects_and_china_strings_share_one_clock(self):
        for value in (NOW, NOW*1000, str(int(NOW*1000)), {'time':NOW*1000},'2026-11-13 12:00:00','2026-11-13T04:00:00Z'):
            self.assertEqual(timestamp(value),NOW)

    def test_start_is_inclusive_and_end_is_exclusive(self):
        self.assertEqual(window({'startTime':NOW,'endTime':NOW+1},now=NOW)['status'],'open')
        self.assertEqual(window({'startTime':NOW-1,'endTime':NOW},now=NOW)['status'],'ended')
        self.assertEqual(window({'startTime':NOW+1,'endTime':NOW+2},now=NOW)['status'],'not_started')

    def test_ended_detail_overrides_list_status_and_missing_deadline(self):
        old={'status':1,'startTime':1758868045000,'endTime':''}
        self.assertEqual(window(old,{'status':1,'isOver':1,'timeLong':-1},now=NOW)['status'],'ended')
        self.assertEqual(window({'status':1,'startTime':NOW-20,'endTime':NOW+60},{'isOver':True},now=NOW)['status'],'ended')

    def test_missing_or_invalid_times_are_not_guessed_as_current(self):
        for value in ({},{'startTime':NOW-1},{'startTime':NOW-1,'endTime':'bad'}, {'startTime':NOW,'endTime':NOW}, {'startTime':False,'endTime':NOW+10}):
            self.assertEqual(window(value,now=NOW)['status'],'unknown')

    def test_indefinite_requires_explicit_nonexpired_platform_proof(self):
        start={'startTime':NOW-60,'endTime':None,'timeLong':-1}
        self.assertEqual(window(start,now=NOW)['status'],'unknown')
        self.assertEqual(window(start,{'setEndTimeNull':1,'isOver':0},now=NOW)['status'],'open')
        self.assertEqual(window(start,{'setEndTimeNull':1,'isOver':1},now=NOW)['status'],'ended')

    def test_conflicting_windows_use_the_conservative_intersection(self):
        self.assertEqual(window({'startTime':NOW-50,'endTime':NOW+100},{'starttime':NOW-30,'endtime':NOW-1},now=NOW)['status'],'ended')
        self.assertEqual(window({'startTime':NOW-50,'endTime':NOW+100},{'starttime':NOW+10,'endtime':NOW+90},now=NOW)['status'],'not_started')


class AttendanceAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_collection_rejects_ended_future_signed_and_keeps_only_current_unsigned(self):
        course={'course_id':'1','class_id':'2','course_name':'Course'}
        client=AttendanceClient({})
        candidates=[{'activeType':2,'status':1,'id':index,'otherId':2,'startTime':NOW-60,'endTime':NOW+60} for index in range(1,6)]
        client.json=AsyncMock(side_effect=[{'result':1,'data':{'activeList':candidates}},
            {'status':1,'isOver':1,'startTime':NOW-60,'endTime':NOW+60},
            {'status':1,'startTime':NOW+10,'endTime':NOW+60},
            {'status':1,'startTime':NOW-60,'endTime':NOW-1},
            {'status':1,'startTime':{'time':NOW*1000-60000},'endTime':{'time':NOW*1000+60000}}, {'result':1,'data':{'status':1}},
            {'status':1,'startTime':NOW-60,'endTime':NOW+60}, {'result':1,'data':{'status':0}}])
        with patch('app.services.attendance.availability.time.time',return_value=NOW): records=await client.activities(course)
        self.assertEqual([item['upstream_id'] for item in records],['5'])
        self.assertEqual(records[0]['start_time'],NOW-60); self.assertEqual(records[0]['end_time'],NOW+60)
        self.assertEqual(records[0]['availability_status'],'open')

    async def test_unknown_deadline_is_a_reported_course_error(self):
        client=AttendanceClient({})
        client.json=AsyncMock(side_effect=[{'result':1,'data':{'activeList':[{'activeType':2,'status':1,'id':1,'startTime':NOW-60}]}},{'status':1,'startTime':NOW-60}])
        with patch('app.services.attendance.availability.time.time',return_value=NOW):
            with self.assertRaises(AttendanceError): await client.activities({'course_id':'1','class_id':'2','course_name':'Course'})

    async def test_crossing_the_deadline_before_submit_never_sends_the_request(self):
        client=AttendanceClient({}); client.context=SimpleNamespace(post=AsyncMock())
        with patch('app.services.attendance.availability.time.time',return_value=NOW):
            result=await client.submit({'params':{'activeId':'1'},'availability':{'start_time':NOW-30,'end_time':NOW}})
        self.assertEqual(result['status'],'expired'); client.context.post.assert_not_awaited()

    async def test_cache_expiration_blocks_old_selections_without_deleting_batch_history(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=Settings(data_root=Path(folder)); settings.ensure_directories(); db=Database(settings); db.initialize()
            account=db.create_account('Student','chaoxing',settings); db.update_account(account['id'],status='ready')
            store=AttendanceStore(db); store.initialize()
            records=store.save_activities(account['id'],[{'upstream_id':'1','course_id':'2','class_id':'3','course_name':'Course','title':'Sign','kind':'normal','start_time':time.time()-10,'end_time':time.time()+60,'availability_status':'open','verified_at':utc_now()}],[('2','3')])
            identifier=records[0]['id']; batch=store.create_batch([identifier])
            with db.connect() as connection: connection.execute('UPDATE attendance_activities SET end_time=? WHERE id=?',(time.time()-1,identifier))
            self.assertEqual(store.activities(),[])
            with self.assertRaises(AttendanceError): store.create_batch([identifier])
            self.assertEqual(store.batch(batch['id'])['total'],1)

    async def test_legacy_cache_requires_reverification_and_is_preserved_as_history(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=Settings(data_root=Path(folder)); settings.ensure_directories(); db=Database(settings); db.initialize()
            account=db.create_account('Student','chaoxing',settings)
            with db.connect() as connection:
                connection.execute('CREATE TABLE attendance_activities(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,upstream_id TEXT NOT NULL,course_id TEXT NOT NULL,class_id TEXT NOT NULL,course_name TEXT NOT NULL,title TEXT NOT NULL,kind TEXT NOT NULL,start_time TEXT,active INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL,UNIQUE(account_id,upstream_id,course_id,class_id))')
                connection.execute('INSERT INTO attendance_activities VALUES(?,?,?,?,?,?,?,?,?,?,?)',('old',account['id'],'1','2','3','Old course','Old sign','qr','1758868045000',1,utc_now()))
            store=AttendanceStore(db); store.initialize()
            self.assertEqual(store.activities(),[])
            with db.connect() as connection: row=connection.execute('SELECT active,availability_status FROM attendance_activities WHERE id=?',('old',)).fetchone()
            self.assertEqual(row['active'],0); self.assertEqual(row['availability_status'],'unverified')
