from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from io import BytesIO
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from PIL import Image, ImageDraw

from app.config import Settings
from app.db import Database, utc_now
from app.services.attendance.class_scan import reset_class_scans
from app.services.attendance.contracts import AttendanceError, SignInput, classify_receipt, parse_academic_year, parse_activities, parse_courses, parse_qr_payload, qr_credentials
from app.services.attendance.client import AttendanceClient
from app.services.attendance.coordinates import wgs84_to_bd09
from app.services.attendance.service import (
    AttendanceService,
    adaptive_sign_concurrency,
    auto_group_effective_interval,
    auto_group_jitter_slot,
    auto_group_scan_due,
    auto_group_scan_phase,
    auto_group_scan_window_open,
    group_scan_window_open,
)
from app.services.attendance.routes import attendance_router, public_share_batch
from app.services.attendance.relay import (
    GROUP_IDLE_POLL_SECONDS,
    OFFICIAL_RELAY_BASE_URL,
    RelayClient,
    RelayStore,
    RemoteShareGateway,
    _avatar_data,
    relay_app,
    relay_monitor_delay,
)


def course(): return {'course_id':'101','class_id':'202','course_name':'Test classroom','class_name':'C806','academic_year':None}
def activity(number='303', kind='normal'): return {**course(),'upstream_id':number,'title':'Test attendance','kind':kind,
    'start_time':time.time()-60,'end_time':time.time()+3600,'availability_status':'open','verified_at':utc_now(),'open_ended':False}


class AttendanceContractTests(unittest.TestCase):
    def test_share_page_uses_mumu_identity_and_a_single_scanner_entry(self):
        source=(Path(__file__).parents[1]/'app/services/attendance/share.html').read_text(encoding='utf-8')
        self.assertIn('data:image/png;base64,',source)
        self.assertIn('id="scanner"',source)
        self.assertIn('item.avatar',source)
        self.assertIn('CAPTURE_HOLD_MS = 1900',source)
        self.assertNotIn('id="message"',source)
        self.assertNotIn('id="manual"',source)
        self.assertNotIn('<textarea',source)
        self.assertNotIn('<footer',source)
        self.assertNotIn('可随时在 Mumu 中撤销',source)
        self.assertNotIn('或上传二维码图片',source)
        self.assertNotIn('或粘贴二维码内容',source)

    def test_location_input_requires_finite_coordinates_and_a_real_address_label(self):
        from pydantic import ValidationError
        valid = {'latitude':30.5,'longitude':114.3,'address':' Test classroom ','source':'browser'}
        self.assertEqual(SignInput(location=valid).location.address,'Test classroom')
        self.assertIsNone(SignInput().location)
        for invalid in ({'latitude':91},{'longitude':181},{'latitude':float('nan')},
                        {'longitude':float('inf')},{'address':'  '},{'source':'teacher_target'}):
            with self.assertRaises(ValidationError): SignInput(location={**valid,**invalid})

    def test_receipt_must_be_explicit_and_cannot_use_success_substring(self):
        for text in ('success','签到成功'): self.assertEqual(classify_receipt(text)[0],'succeeded')
        self.assertEqual(classify_receipt('success2')[0],'succeeded')
        self.assertEqual(classify_receipt('您已签到')[0],'already_signed')
        self.assertEqual(classify_receipt('签到失败[90002]')[0],'awaiting_input')
        self.assertEqual(classify_receipt('photoAuthError_PCR007')[0],'manual_required')
        self.assertIn('现场拍摄',classify_receipt('photoAuthError_PCR007')[1])
        self.assertEqual(classify_receipt('errorLocation_12.3')[0],'failed')
        self.assertNotEqual(classify_receipt('签到不成功')[0],'succeeded')
        self.assertNotEqual(classify_receipt('not success')[0],'succeeded')
        self.assertEqual(classify_receipt('validate_required')[0],'manual_required')
        self.assertEqual(classify_receipt('二维码已过期')[0],'awaiting_input')
        self.assertEqual(classify_receipt('签到已结束')[0],'expired')
        self.assertEqual(classify_receipt('<html>unexpected</html>')[0],'unknown')

    def test_relay_monitor_backoff_is_stable_capped_and_escalating(self):
        delays=[relay_monitor_delay('remote-group',failures) for failures in range(1,9)]
        self.assertEqual(delays,list(sorted(delays)))
        self.assertEqual(delays[0],relay_monitor_delay('remote-group',1))
        self.assertGreaterEqual(delays[0],1)
        self.assertLess(delays[0],1.25)
        self.assertEqual(delays[-1],30)

    def test_qr_is_bound_to_an_activity_and_is_never_a_fetchable_arbitrary_url(self):
        text='https://mobilelearn.chaoxing.com/widget/sign/e?activePrimaryId=303&enc=abcdefgh1234'
        self.assertEqual(qr_credentials(text,'303'),{'enc':'abcdefgh1234'})
        self.assertEqual(parse_qr_payload(text),{'active_id':'303','enc':'abcdefgh1234'})
        self.assertEqual(qr_credentials('{"activeId":"303","enc":"abcdefgh1234"}','303'),{'enc':'abcdefgh1234'})
        for value in ('http://127.0.0.1/a?activeId=303&enc=abcdefgh1234','https://evil.example/?activeId=303&enc=abcdefgh1234','https://mobilelearn.chaoxing.com/a?activeId=404&enc=abcdefgh1234','null'):
            with self.assertRaises(AttendanceError): qr_credentials(value,'303')

    def test_collection_only_accepts_explicit_ongoing_attendance(self):
        payload={'result':1,'data':{'activeList':[{'activeType':2,'status':1,'id':303,'otherId':2}, {'activeType':2,'status':2,'id':304}, {'activeType':19,'status':1,'id':305}]}}
        records=parse_activities(payload,course())
        self.assertEqual(len(records),1); self.assertEqual(records[0]['kind'],'qr')
        inherited=parse_activities(payload,{**course(),'status':'running','open_ended':1,'verified_at':'old','availability_status':'ended'})[0]
        self.assertNotIn('status',inherited); self.assertNotIn('open_ended',inherited); self.assertNotIn('availability_status',inherited)
        self.assertEqual(parse_activities({'result':1,'data':None},course()),[])
        self.assertEqual(parse_activities({'result':1,'data':{}},course()),[])
        for value in ({},{'result':0,'data':{'activeList':[]}}):
            with self.assertRaises(AttendanceError): parse_activities(value,course())

    def test_encrypted_gesture_code_is_an_accepted_input_shape(self):
        code='AilJpeOmQAA6CCTI81DWdg=='
        self.assertEqual(SignInput(code=code).code,code)

    def test_courses_are_account_scoped_class_pairs_and_malformed_data_does_not_become_empty(self):
        payload={'result':1,'channelList':[{'content':{'id':202,'name':'(2026-2027-1)-08120080-01','state':0,'course':{'data':[{'id':101,'name':'Course'}]}}},{'content':{'id':203,'state':1,'course':{'data':[{'id':101}]}}}]}
        self.assertEqual(parse_courses(payload),[{
            'course_id':'101','class_id':'202','course_name':'Course',
            'class_name':'(2026-2027-1)-08120080-01','academic_year':2026,
            'begin_at':'','end_at':'','is_retired':False,
        }])
        self.assertEqual(parse_academic_year('（26-27学年）第1学期'),2026)
        self.assertIsNone(parse_academic_year('C806'))
        with self.assertRaises(AttendanceError): parse_courses({'result':False,'channelList':[]})
        with self.assertRaises(Exception): SignInput(code='guess')
        with self.assertRaises(Exception): SignInput(code='123',account_id='forged')


class AttendanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.settings=Settings(data_root=Path(self.temp.name)); self.settings.ensure_directories()
        self.db=Database(self.settings); self.db.initialize()
        self.accounts=[self.db.create_account('User '+str(index),'chaoxing',self.settings) for index in range(2)]
        for index,account in enumerate(self.accounts): self.db.update_account(account['id'],status='ready',platform_user_id=str(index+1000))
        self.submissions=[]; self.statuses={}; owner=self
        class FakeClient:
            def __init__(self,account): self.account=account
            async def __aenter__(self): return self
            async def __aexit__(self,*args):
                if getattr(owner,'cleanup_error',False): raise RuntimeError('Test transport cleanup failure')
            async def courses(self): return [course()]
            async def activities(self,current): return [activity()]
            async def prepare(self,item,inputs):
                outcome=owner.statuses.get(item['account_id'],'ready')
                if outcome=='error': raise AttendanceError('登录已失效')
                if outcome=='timeout': outcome='ready'
                return {'status':outcome,'message':'Test result','params':{'uid':self.account.get('platform_user_id')}}
            async def submit(self,prepared):
                owner.submissions.append(self.account['id'])
                if owner.statuses.get(self.account['id'])=='timeout': raise TimeoutError()
                return {'status':'succeeded','message':'平台已确认签到成功'}
        self.service=AttendanceService(lambda:self.db,FakeClient); self.service.initialize()
        self.rows=[]
        for account in self.accounts: self.rows.extend(self.service.store.save_activities(account['id'],[activity()],[('101','202')]))

    async def asyncTearDown(self): await self.service.shutdown(); self.temp.cleanup()
    def batch(self): return self.service.store.create_batch([row['id'] for row in self.rows])
    async def finished(self,identifier): await self.service.tasks[identifier]; return self.service.store.batch(identifier)

    async def test_verified_batch_receipts_survive_cleanup_errors_and_repeated_execution(self):
        self.cleanup_error=True
        batch=self.batch(); self.service.start(batch['id'],SignInput()); result=await self.finished(batch['id'])
        self.assertEqual(result['completed'],2); self.assertEqual(result['status'],'completed')
        self.assertEqual(self.service.store.activities(),[])
        self.service.start(batch['id'],SignInput()); self.assertEqual(len(self.submissions),2)

    async def test_overlapping_batches_do_not_submit_the_same_activity_twice(self):
        one,two=self.batch(),self.batch()
        self.service.start(one['id'],SignInput()); self.service.start(two['id'],SignInput())
        await asyncio.gather(self.service.tasks[one['id']],self.service.tasks[two['id']])
        self.assertEqual(len(self.submissions),2)
        self.assertTrue(all(item['status']=='already_signed' for item in self.service.store.batch(two['id'])['items']))

    async def test_one_account_failure_does_not_stop_the_remaining_accounts(self):
        self.statuses[self.accounts[0]['id']]='error'; batch=self.batch()
        self.service.start(batch['id'],SignInput()); result=await self.finished(batch['id'])
        self.assertEqual(result['completed'],1); self.assertEqual(len(self.submissions),1)
        self.assertIn('auth_required',[item['status'] for item in result['items']])

    async def test_network_timeout_after_dispatch_is_unknown_and_is_never_automatically_replayed(self):
        self.statuses[self.accounts[0]['id']]='timeout'; batch=self.batch()
        self.service.start(batch['id'],SignInput()); result=await self.finished(batch['id'])
        self.assertIn('unknown',[item['status'] for item in result['items']]); self.assertEqual(len(self.submissions),2)
        with self.assertRaises(AttendanceError): self.service.start(batch['id'],SignInput())
        self.assertEqual(len(self.submissions),2)

    async def test_api_request_context_submits_once_and_does_not_claim_ambiguous_receipts(self):
        client=AttendanceClient({}); response=SimpleNamespace(ok=True,text=AsyncMock(return_value='not success'))
        client.context=SimpleNamespace(post=AsyncMock(return_value=response))
        result=await client.submit({'params':{'activeId':'303','uid':'1000'},'availability':activity()})
        self.assertEqual(result['status'],'unknown'); client.context.post.assert_awaited_once()
        self.assertEqual(client.context.post.await_args.kwargs['max_retries'],0)

    async def test_missing_input_is_preserved_for_later_completion(self):
        self.statuses[self.accounts[0]['id']]='awaiting_input'; batch=self.batch()
        self.service.start(batch['id'],SignInput()); result=await self.finished(batch['id'])
        self.assertEqual(result['status'],'awaiting_input'); self.assertEqual(result['completed'],1)
        self.statuses.clear(); self.service.start(batch['id'],SignInput(code='1234')); result=await self.finished(batch['id'])
        self.assertEqual(result['completed'],2); self.assertEqual(len(self.submissions),2)

    async def test_unknown_submission_blocks_retries_and_overlapping_batches_until_reconciled(self):
        batch=self.batch(); second=self.batch(); identifier=batch['items'][0]['id']
        self.service.store.update_item(identifier,'unknown','No receipt')
        with self.assertRaises(AttendanceError): self.service.start(batch['id'],SignInput())
        with self.assertRaises(AttendanceError): self.batch()
        self.service.start(second['id'],SignInput()); result=await self.finished(second['id'])
        self.assertEqual(sum(item['status']=='unknown' for item in result['items']),1)
        self.assertEqual(len(self.submissions),1)
        self.statuses[batch['items'][0]['account_id']]='already_signed'
        result=await self.service.reconcile(batch['id'])
        self.assertEqual(result['items'][0]['status'],'already_signed')

    async def test_restart_marks_submission_unknown_and_requeues_unsent_work(self):
        batch=self.batch(); self.service.store.update_item(batch['items'][0]['id'],'submitting','Waiting')
        self.service.store.update_item(batch['items'][1]['id'],'queued','Waiting')
        self.service.initialize(); states=[item['status'] for item in self.service.store.batch(batch['id'])['items']]
        self.assertEqual(states,['unknown','pending']); self.assertEqual(self.submissions,[])

    async def test_long_link_groups_store_encrypted_links_and_fixed_account_scope(self):
        ids=[account['id'] for account in self.accounts]
        group=self.service.store.create_qr_group('计算机科学 1 组',ids,'remote-group','ciphertext',True)
        self.assertEqual(group['name'],'计算机科学 1 组')
        self.assertEqual(group['account_ids'],ids)
        self.assertEqual(len(group['participants']),2)
        self.assertTrue(group['requires_pin'])
        with self.db.connect() as db:
            raw=dict(db.execute('SELECT * FROM attendance_qr_groups WHERE id=?',(group['id'],)).fetchone())
        self.assertEqual(raw['link_ciphertext'],'ciphertext')
        self.assertNotIn('https://',str(raw))
        self.service.store.revoke_qr_group(group['id'])
        self.assertTrue(self.service.store.qr_group(group['id'])['revoked'])

    async def test_account_assets_global_location_and_auto_groups_round_trip(self):
        path=Path(self.temp.name)/'attendance-assets'/'photo.png'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'\x89PNG\r\n\x1a\nphoto')
        assets=self.service.store.save_account_asset(self.accounts[0]['id'],'photo',path,'image/png')
        self.assertEqual(assets['photo_path'],str(path))
        location=self.service.store.save_global_location({'latitude':30.5,'longitude':114.3,'address':'教学楼 A 区'})
        self.assertEqual(location['address'],'教学楼 A 区')
        group=self.service.store.create_auto_group(
            '计算机科学自动组',[self.accounts[0]['id']],['normal','photo','code'],120,True,None,
            course_filters=['计算机科学','软件工程'],
            windows=[{'days':[1,3,5],'start':'08:00','end':'09:30'}],
        )
        self.assertEqual(group['kinds'],['normal','photo','code'])
        self.assertEqual(group['course_filters'],['计算机科学','软件工程'])
        self.assertEqual(group['windows'],[{'days':[1,3,5],'start':'08:00','end':'09:30'}])
        self.assertEqual(group['participants'][0]['id'],self.accounts[0]['id'])
        updated=self.service.store.save_auto_group(group['id'],enabled=False,name='自动组 2')
        self.assertFalse(updated['enabled']); self.assertEqual(updated['name'],'自动组 2')
        self.service.store.delete_auto_group(group['id'])
        self.assertEqual(self.service.store.auto_groups(),[])

    async def test_new_auto_group_defaults_to_thirty_second_scan_interval(self):
        group=self.service.store.create_auto_group(
            '默认间隔组',
            [self.accounts[0]['id']],
            ['normal'],
        )
        self.assertEqual(group['interval_seconds'],30)

    async def test_auto_group_materials_are_validated_by_sign_in_kind(self):
        account_id=self.accounts[0]['id']
        with self.assertRaisesRegex(AttendanceError,'拍照签到缺少账号照片素材'):
            self.service.store.create_auto_group('拍照组',[account_id],['photo'],60,True,None)
        with self.assertRaisesRegex(AttendanceError,'位置签到需要先设置位置签到素材'):
            self.service.store.create_auto_group('位置组',[account_id],['location'],60,True,None)
        location=self.service.store.save_global_location({'latitude':30.5,'longitude':114.3,'address':'教学楼'})
        group=self.service.store.create_auto_group('位置组',[account_id],['location'],60,True,None)
        self.assertEqual(group['kinds'],['location'])
        self.assertEqual(location['address'],'教学楼')

    async def test_auto_group_backs_off_when_platform_throttles_collection(self):
        class ThrottleClient:
            def __init__(self, account): self.account=account
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return None
            async def courses(self): return [course()]
            async def activities(self,current): raise AttendanceError('平台请求频繁，请稍后重试')

        group=self.service.store.create_auto_group('限流组',[self.accounts[0]['id']],['normal'],30,True,None)
        self.service.client_factory=ThrottleClient
        await self.service._auto_group(group)
        throttle=self.service.store.account_throttle(self.accounts[0]['id'])
        self.assertEqual(throttle['failures'],1)
        self.assertGreater(throttle['cooldown_until'],time.time()+50)
        saved=self.service.store.auto_group(group['id'])
        self.assertTrue(saved['all_cooling'])
        error=saved['last_error']
        self.assertTrue('频繁' in error or '限流' in error)
        with self.assertRaisesRegex(AttendanceError,'所有账号仍在限流冷却'):
            await self.service.scan_auto_group_now(group['id'])
        await self.service._auto_group(group)
        self.assertEqual(self.service.store.account_throttle(self.accounts[0]['id'])['failures'],1)

    async def test_account_throttle_escalates_to_thirty_minutes_and_success_resets(self):
        account_id=self.accounts[0]['id']
        delays=[]
        for _ in range(8):
            state=self.service.store.note_account_throttle(account_id,now=1000)
            delays.append(state['delay_seconds'])
        self.assertEqual(delays,[60,120,240,480,960,1800,1800,1800])
        self.service.store.clear_account_throttle(account_id)
        self.assertEqual(self.service.store.account_throttle(account_id)['failures'],0)

    def test_request_budget_is_persisted_per_hour_and_separates_scan_from_sign(self):
        account_id=self.accounts[0]['id']
        first_hour=datetime(2026,11,14,10,tzinfo=timezone.utc)
        next_hour=datetime(2026,11,14,11,tzinfo=timezone.utc)
        self.service.store.charge_request_budget(account_id,'scan',scan_limit=2,sign_limit=1,now=first_hour)
        self.service.store.charge_request_budget(account_id,'scan',scan_limit=2,sign_limit=1,now=first_hour)
        with self.assertRaisesRegex(AttendanceError,'扫描额度已用完'):
            self.service.store.charge_request_budget(account_id,'scan',scan_limit=2,sign_limit=1,now=first_hour)
        current=self.service.store.request_budget(account_id,now=first_hour)
        self.assertEqual((current['scan_count'],current['sign_count']),(2,0))
        reset=self.service.store.request_budget(account_id,now=next_hour)
        self.assertEqual((reset['scan_count'],reset['sign_count']),(0,0))

    async def test_service_request_guard_applies_budget_before_global_pacing(self):
        account_id=self.accounts[0]['id']
        self.service.request_pacer=SimpleNamespace(acquire=AsyncMock())
        await self.service.guard_request(account_id,'scan')
        self.assertEqual(self.service.store.request_budget(account_id)['scan_count'],1)
        self.service.request_pacer.acquire.assert_awaited_once_with('scan')

    def test_class_scan_state_and_summary_survive_store_recreation(self):
        account_id=self.accounts[0]['id']
        state={
            'class_key':'course-persist:class-persist','course_id':'course-persist',
            'class_id':'class-persist','course_name':'计算机科学','class_name':'C806',
            'members':[account_id],'anchor':account_id,'idle_streak':3,
            'failure_streak':2,'backoff_seconds':240,'backoff_until':time.time()+120,
            'next_due_at':time.time()+300,'last_scan_at':time.time()-60,
            'last_error':'平台请求频繁','scan_count':4,'anchor_scans':2,
            'fallback_scans':1,'activity_count':0,'anchor_switches':2,
        }
        self.service.store.save_class_scan_state(state)
        loaded=self.service.store.class_scan_state(state['class_key'])
        self.assertEqual(loaded['members'],[account_id])
        self.assertEqual(loaded['idle_streak'],3)
        self.assertEqual(loaded['anchor'],account_id)
        self.assertEqual(loaded['anchor_switches'],2)
        summary=self.service.store.class_scan_summary([account_id])
        self.assertEqual(summary['classes'],1)
        self.assertEqual(summary['anchored'],1)
        self.assertEqual(summary['cold_classes'],1)
        self.assertEqual(summary['cooling_classes'],1)
        self.assertEqual(summary['scans'],4)
        self.assertEqual(summary['anchor_switches'],2)

    def test_code_attempt_lock_is_persisted_per_account_and_activity(self):
        account_id=self.accounts[0]['id']
        store=self.service.store
        start=1_800_000_000
        first=store.note_code_attempt(account_id,'active-code',now=start)
        second=store.note_code_attempt(account_id,'active-code',now=start+10)
        third=store.note_code_attempt(account_id,'active-code',now=start+20)
        self.assertEqual([first['wrong_count'],second['wrong_count'],third['wrong_count']],[1,2,3])
        self.assertEqual(third['locked_until'],start+3620)
        self.assertEqual(store.code_attempt(account_id,'active-code',now=start+30)['wrong_count'],3)
        reset=store.code_attempt(account_id,'active-code',now=start+4000)
        self.assertEqual((reset['wrong_count'],reset['locked_until']),(0,0))
        other=store.code_attempt(account_id,'other-code',now=start)
        self.assertEqual((other['wrong_count'],other['locked_until']),(0,0))

    async def test_service_client_blocks_a_persisted_locked_code_activity(self):
        self.service.client_factory=AttendanceClient
        account_id=self.accounts[0]['id']
        self.service.store.note_code_attempt(account_id,'locked-activity',locked=True)
        client=self.service.client(self.db.get_account(account_id))
        client.json=AsyncMock(side_effect=AssertionError('locked code activity must not call the platform'))
        valid,message=await client._check_code('locked-activity','any-code')
        self.assertFalse(valid)
        self.assertIn('停止校验',message)
        client.json.assert_not_awaited()

    async def test_attendance_client_restores_persisted_class_schedule_without_upstream_scan(self):
        self.addCleanup(reset_class_scans)
        reset_class_scans()
        account_id=self.accounts[0]['id']
        self.service.store.save_class_scan_state({
            'class_key':'101:202','course_id':'101','class_id':'202',
            'course_name':'Test classroom','class_name':'C806',
            'members':[account_id],'anchor':account_id,'idle_streak':3,
            'failure_streak':0,'backoff_seconds':0,'backoff_until':0,
            'next_due_at':time.time()+300,'last_scan_at':time.time()-60,
            'last_error':'','scan_count':2,'anchor_scans':2,
            'fallback_scans':0,'activity_count':0,
        })
        self.service.client_factory=AttendanceClient
        client=self.service.client(self.db.get_account(account_id))
        client.json=AsyncMock(side_effect=AssertionError('restored class schedule should reuse its cached empty result'))
        rows=await client.class_activities(course())
        self.assertEqual(rows,[])
        client.json.assert_not_awaited()

    def test_group_scan_windows_support_weekdays_and_overnight_ranges(self):
        timezone_cn=ZoneInfo('Asia/Shanghai')
        regular={'windows':[{'days':[1,3,5],'start':'08:00','end':'09:30'}]}
        self.assertTrue(group_scan_window_open(regular,datetime(2026,11,16,8,30,tzinfo=timezone_cn)))
        self.assertFalse(group_scan_window_open(regular,datetime(2026,11,16,9,30,tzinfo=timezone_cn)))
        overnight={'windows':[{'days':[1],'start':'23:00','end':'01:00'}]}
        self.assertTrue(group_scan_window_open(overnight,datetime(2026,11,16,23,30,tzinfo=timezone_cn)))
        self.assertTrue(group_scan_window_open(overnight,datetime(2026,11,17,0,30,tzinfo=timezone_cn)))
        self.assertFalse(group_scan_window_open(overnight,datetime(2026,11,17,1,0,tzinfo=timezone_cn)))
        self.assertFalse(auto_group_scan_window_open({'windows':[]},datetime(2026,11,16,23,30,tzinfo=timezone_cn)))
        self.assertTrue(auto_group_scan_window_open(overnight,datetime(2026,11,16,23,30,tzinfo=timezone_cn)))

    def test_auto_group_jitter_is_stable_and_distributed(self):
        first=auto_group_jitter_slot('group-one')
        self.assertEqual(first,auto_group_jitter_slot('group-one'))
        self.assertIn(first,range(5))
        slots={auto_group_jitter_slot(f'group-{index}') for index in range(50)}
        self.assertEqual(slots,set(range(5)))

    def test_auto_group_phase_prewarms_bursts_and_handles_overnight_windows(self):
        timezone_cn=ZoneInfo('Asia/Shanghai')
        group={
            'interval_seconds':60,
            'windows':[{'days':[1],'start':'10:00','end':'10:10'}],
        }
        outside=auto_group_scan_phase(group,datetime(2026,11,16,9,58,59,tzinfo=timezone_cn))
        prewarm=auto_group_scan_phase(group,datetime(2026,11,16,9,59,30,tzinfo=timezone_cn))
        burst=auto_group_scan_phase(group,datetime(2026,11,16,10,0,10,tzinfo=timezone_cn))
        steady=auto_group_scan_phase(group,datetime(2026,11,16,10,3,1,tzinfo=timezone_cn))
        ended=auto_group_scan_phase(group,datetime(2026,11,16,10,10,0,tzinfo=timezone_cn))
        self.assertEqual(outside['mode'],'outside')
        self.assertEqual(prewarm['mode'],'prewarm')
        self.assertEqual(burst['mode'],'burst')
        self.assertEqual(steady['mode'],'normal')
        self.assertEqual(ended['mode'],'outside')
        self.assertEqual(auto_group_effective_interval(group,'prewarm'),20)
        self.assertEqual(auto_group_effective_interval(group,'burst'),5)
        overnight={
            'interval_seconds':300,
            'windows':[{'days':[1],'start':'23:00','end':'01:00'}],
        }
        self.assertEqual(
            auto_group_scan_phase(overnight,datetime(2026,11,16,23,1,tzinfo=timezone_cn))['mode'],
            'burst',
        )
        self.assertEqual(
            auto_group_scan_phase(overnight,datetime(2026,11,17,0,30,tzinfo=timezone_cn))['mode'],
            'normal',
        )
        self.assertEqual(
            auto_group_scan_phase({'windows':[]},datetime(2026,11,16,23,30,tzinfo=timezone_cn))['mode'],
            'outside',
        )

    def test_expected_attendance_warning_logs_are_throttled_per_course_error(self):
        key=('account','course','class','平台请求频繁')
        self.assertTrue(self.service.warning_log_due(key,300,now=1000))
        self.assertFalse(self.service.warning_log_due(key,300,now=1299))
        self.assertTrue(self.service.warning_log_due(key,300,now=1300))

    def test_auto_group_due_uses_initial_jitter_without_throttling_each_interval(self):
        group={'id':'group-one','created_at':100,'last_scan_at':0}
        initial=100+auto_group_jitter_slot('group-one')
        self.assertFalse(auto_group_scan_due(group,5,now=initial-.01))
        self.assertTrue(auto_group_scan_due(group,5,now=initial))
        group['last_scan_at']=200
        self.assertFalse(auto_group_scan_due(group,5,now=204.9))
        self.assertTrue(auto_group_scan_due(group,5,now=205))

    def test_adaptive_sign_concurrency_tracks_backlog_deadline_and_unique_accounts(self):
        now=time.time()
        items=[
            {'account_id':f'account-{index}','end_time':now+18}
            for index in range(100)
        ]
        self.assertEqual(
            adaptive_sign_concurrency(items,max_concurrency=8,rush=True,now=now),
            8,
        )
        self.assertEqual(
            adaptive_sign_concurrency(items[:2],max_concurrency=8,rush=True,now=now),
            2,
        )
        relaxed=[
            {'account_id':f'account-{index}','end_time':now+600}
            for index in range(20)
        ]
        self.assertEqual(
            adaptive_sign_concurrency(relaxed,max_concurrency=8,now=now),
            3,
        )

    async def test_batch_execution_uses_adaptive_worker_limit(self):
        now=time.time()
        items=[
            {'id':f'item-{index}','account_id':f'account-{index}','kind':'normal','end_time':now+18}
            for index in range(12)
        ]
        active=peak=0
        async def observe(*args):
            nonlocal active,peak
            active+=1
            peak=max(peak,active)
            await asyncio.sleep(.01)
            active-=1
        self.service.sign_concurrency_limit=4
        self.service.slots=asyncio.Semaphore(4)
        self.service._one=observe
        with patch.object(type(self.service.store),'item_status',return_value='running'):
            await self.service._run('batch',items,SignInput(),None,rush=True)
        self.assertEqual(peak,4)

    async def test_shared_code_discovered_later_in_batch_retries_earlier_account(self):
        recipients=[item['id'] for item in self.accounts]
        code_activity=activity('505',kind='code')
        rows=[]
        for account_id in recipients:
            rows.extend(
                self.service.store.save_activities(
                    account_id,
                    [code_activity],
                    [('101','202')],
                )
            )
        batch=self.service.store.create_batch([row['id'] for row in rows])
        attempts={account_id:0 for account_id in recipients}
        owner=self

        class SharedCodeClient:
            def __init__(self,account): self.account=account
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return None
            async def prepare(self,item,inputs):
                attempts[self.account['id']]+=1
                if (
                    self.account['id']==recipients[0]
                    and attempts[self.account['id']]==1
                ):
                    return {
                        'status':'awaiting_input',
                        'message':'当前账号没有读取签到码的权限',
                    }
                if self.account['id']==recipients[1]:
                    owner.service.store.codebook_put(
                        str(item['upstream_id']),
                        '9999',
                        'activity',
                        'code',
                    )
                return {
                    'status':'ready',
                    'message':'',
                    'kind':'code',
                    'params':{'activeId':item['upstream_id'],'signCode':'9999'},
                }
            async def submit(self,prepared):
                return {'status':'succeeded','message':'平台已确认签到成功'}

        self.service.client_factory=SharedCodeClient
        self.service.start(batch['id'],SignInput())
        result=await self.finished(batch['id'])
        self.assertEqual(result['completed'],2)
        self.assertEqual(attempts,{account_id:1 for account_id in recipients} | {recipients[0]:2})
        self.assertEqual(self.service.store.codebook_get('505')['code'],'9999')

    async def test_restart_recovery_requeues_unsubmitted_items_and_keeps_submitting_unknown(self):
        batch=self.batch()
        first,second=batch['items']
        self.service.store.update_item(first['id'],'running','测试中断')
        self.service.store.update_item(second['id'],'submitting','测试提交中断')
        recovered=self.service.store.recover()
        self.assertIn(batch['id'],recovered)
        self.assertEqual(self.service.store.item_status(first['id']),'pending')
        self.assertEqual(self.service.store.item_status(second['id']),'unknown')
        saved=self.service.store.batch(batch['id'])
        self.assertIn('恢复执行',next(item['message'] for item in saved['items'] if item['id']==first['id']))
        self.assertIn('核对平台记录',next(item['message'] for item in saved['items'] if item['id']==second['id']))

    async def test_startup_resumes_a_recovered_unsubmitted_batch(self):
        batch=self.batch()
        for item in batch['items']:
            self.service.store.update_item(item['id'],'running','测试服务重启')
        self.service.store.save_global_location({
            'latitude':30.5,
            'longitude':114.3,
            'address':'恢复位置',
            'source':'browser',
        })
        recovered=AttendanceService(lambda:self.db,self.service.client_factory)
        recovered.initialize()
        self.assertIn(batch['id'],recovered.recovered_batch_ids)
        try:
            await recovered._resume_recovered_batches()
            result=await recovered.tasks[batch['id']]
            saved=recovered.store.batch(batch['id'])
        finally:
            await recovered.shutdown()
        self.assertEqual(saved['completed'],2)
        self.assertEqual(len(self.submissions),2)

    async def test_graceful_shutdown_preserves_unsubmitted_work_for_restart(self):
        batch=self.batch()
        self.service.sign_concurrency_limit=1
        self.service.slots=asyncio.Semaphore(1)
        started=asyncio.Event()
        blocker=asyncio.Event()

        class BlockingClient:
            def __init__(self,account): self.account=account
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return None
            async def prepare(self,item,inputs):
                started.set()
                await blocker.wait()
                return {'status':'ready','message':'','params':{'activeId':item['upstream_id']}}
            async def submit(self,prepared):
                return {'status':'succeeded','message':'平台已确认签到成功'}

        self.service.client_factory=BlockingClient
        self.service.start(batch['id'],SignInput())
        await asyncio.wait_for(started.wait(),timeout=1)
        await self.service.shutdown()
        saved=self.service.store.batch(batch['id'])
        self.assertEqual([item['status'] for item in saved['items']],['running','queued'])
        self.assertTrue(all('下次启动恢复' in item['message'] for item in saved['items']))
        self.assertIn(batch['id'],self.service.store.recover())

    async def test_auto_group_skips_upstream_when_scan_budget_is_exhausted(self):
        account_id=self.accounts[0]['id']
        self.service.scan_budget_per_hour=1
        self.service.charge_request_budget(account_id,'scan')
        calls=[]
        class ExplodingClient:
            def __init__(self, account): calls.append(account['id'])
            async def __aenter__(self): raise AttendanceError('client should not open')
            async def __aexit__(self,*args): return None
        self.service.client_factory=ExplodingClient
        group=self.service.store.create_auto_group('额度组',[account_id],['normal'],60,True,None)
        await self.service._auto_group(group)
        self.assertEqual(calls,[])
        self.assertIn('扫描额度已用完',self.service.store.auto_group(group['id'])['last_error'])

    async def test_disabled_auto_group_still_supports_one_manual_scan(self):
        group=self.service.store.create_auto_group(
            '手动检测组',[self.accounts[0]['id']],['normal'],60,False,None,
        )
        result=await self.service.scan_auto_group_now(group['id'])
        self.assertFalse(result['enabled'])
        self.assertTrue(result['last_scan_at'])
        self.assertTrue(result['last_batch_id'])
        batch=await self.finished(result['last_batch_id'])
        self.assertEqual(batch['completed'],1)

    async def test_auto_group_global_concurrency_gate_limits_parallel_groups(self):
        self.service.auto_group_slots=asyncio.Semaphore(1)
        active=0
        peak=0
        async def observe(group,force=False,scan_mode='normal'):
            nonlocal active,peak
            active+=1
            peak=max(peak,active)
            await asyncio.sleep(.02)
            active-=1
        self.service._auto_group_inner=observe
        await asyncio.gather(
            self.service._auto_group({'id':'one'}),
            self.service._auto_group({'id':'two'}),
        )
        self.assertEqual(peak,1)

    async def test_sign_budget_blocks_submission_without_claiming_an_unknown_receipt(self):
        account_id=self.accounts[0]['id']
        self.service.sign_budget_per_hour=1
        self.service.charge_request_budget(account_id,'sign')
        batch=self.batch(); self.service.start(batch['id'],SignInput())
        result=await self.finished(batch['id'])
        item=next(item for item in result['items'] if item['account_id']==account_id)
        self.assertEqual(item['status'],'failed')
        self.assertIn('签到请求额度已用完',item['message'])
        self.assertNotIn(account_id,self.submissions)

    async def test_auto_group_course_filter_limits_scanning_to_matching_courses(self):
        scanned=[]
        class FilterClient:
            def __init__(self, account): self.account=account
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return None
            async def courses(self):
                return [course(),{'course_id':'102','class_id':'203','course_name':'Other classroom'}]
            async def activities(self,current):
                scanned.append(current['course_name'])
                return []

        group=self.service.store.create_auto_group(
            '课程筛选组',[self.accounts[0]['id']],['normal'],course_filter='Test classroom',
        )
        self.service.client_factory=FilterClient
        self.service.store.note_account_throttle(self.accounts[0]['id'],now=0)
        await self.service._auto_group(group)
        self.assertEqual(scanned,['Test classroom'])
        self.assertEqual(self.service.store.account_throttle(self.accounts[0]['id'])['failures'],0)
        saved=self.service.store.auto_group(group['id'])
        self.assertEqual(saved['course_filter'],'Test classroom')
        self.assertEqual(saved['course_filters'],['Test classroom'])

    async def test_auto_group_skips_accounts_without_the_selected_course(self):
        scanned=[]
        accounts=self.accounts
        class FilterClient:
            def __init__(self, account): self.account=account
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return None
            async def courses(self):
                if self.account['id']==accounts[0]['id']:
                    return [course(),{'course_id':'102','class_id':'203','course_name':'Other classroom'}]
                return [{'course_id':'102','class_id':'203','course_name':'Other classroom'}]
            async def activities(self,current):
                scanned.append((self.account['id'],current['course_name']))
                return []

        group=self.service.store.create_auto_group(
            '并集课程组',[item['id'] for item in self.accounts],['normal'],
            course_filter='Test classroom',
        )
        self.service.client_factory=FilterClient
        await self.service._auto_group(group)
        self.assertEqual(scanned,[(self.accounts[0]['id'],'Test classroom')])
        saved=self.service.store.auto_group(group['id'])
        self.assertEqual(saved['last_error'],'')

    async def test_auto_group_does_not_queue_stale_activities_outside_course_filter(self):
        account_id=self.accounts[0]['id']
        other={
            **activity('404'),
            'course_id':'102',
            'class_id':'203',
            'course_name':'Other classroom',
        }
        self.service.store.save_activities(account_id,[other],[('102','203')])
        class FilterClient:
            def __init__(self, account): self.account=account
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return None
            async def courses(self):
                return [course(),{'course_id':'102','class_id':'203','course_name':'Other classroom'}]
            async def activities(self,current): return []

        group=self.service.store.create_auto_group(
            '课程边界组',[account_id],['normal'],course_filter='Test classroom',
        )
        self.service.client_factory=FilterClient
        await self.service._auto_group(group)
        saved=self.service.store.auto_group(group['id'])
        self.assertIsNone(saved['last_batch_id'])
        self.assertEqual(saved['last_error'],'')

    async def test_auto_group_without_filter_scans_only_current_academic_year_courses(self):
        scanned=[]
        class CurrentYearClient:
            def __init__(self, account): self.account=account
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return None
            async def courses(self):
                return [
                    course(),
                    {'course_id':'historical','class_id':'old','course_name':'历史课程',
                     'class_name':'2025-2026-1','academic_year':2025},
                ]
            async def activities(self,current):
                scanned.append(current['course_name'])
                return []

        group=self.service.store.create_auto_group(
            '当前学年组',[self.accounts[0]['id']],['normal'],
        )
        self.service.client_factory=CurrentYearClient
        await self.service._auto_group(group)
        self.assertEqual(scanned,['Test classroom'])

    async def test_auto_group_course_catalog_returns_current_year_union_and_marks_common_courses(self):
        current_courses={
            self.accounts[0]['id']:[
                {'course_id':'101','class_id':'201','course_name':'共有课程','class_name':'(2026-2027-1)-A','academic_year':2026},
                {'course_id':'102','class_id':'202','course_name':'仅账号一','class_name':'未标注学期','academic_year':None},
                {'course_id':'103','class_id':'203','course_name':'历史课程','class_name':'未标注学期','academic_year':None,'begin_at':'2025-09-01 08:00','is_retired':False},
            ],
            self.accounts[1]['id']:[
                {'course_id':'101','class_id':'211','course_name':'共有课程','class_name':'C806','academic_year':None},
                {'course_id':'104','class_id':'204','course_name':'仅账号二','class_name':'(2026-2027-1)-D','academic_year':2026},
            ],
        }
        class CatalogClient:
            def __init__(self, account): self.account=account
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return None
            async def courses(self): return current_courses[self.account['id']]

        self.service.client_factory=CatalogClient
        result=await self.service.course_catalog([account['id'] for account in self.accounts])
        self.assertEqual({item['name'] for item in result['courses']},{'共有课程','仅账号一','仅账号二'})
        common=next(item for item in result['courses'] if item['name']=='共有课程')
        self.assertTrue(common['common'])
        self.assertEqual(common['account_ids'],sorted(account['id'] for account in self.accounts))
        self.assertFalse(next(item for item in result['courses'] if item['name']=='仅账号一')['common'])
        single=await self.service.course_catalog([self.accounts[0]['id']])
        self.assertEqual([item['name'] for item in single['courses']],['仅账号一','共有课程'])

    async def test_auto_group_reports_a_course_filter_that_matches_nothing(self):
        group=self.service.store.create_auto_group(
            '无匹配课程组',[self.accounts[0]['id']],['normal'],course_filter='Missing course',
        )
        await self.service._auto_group(group)
        error=self.service.store.auto_group(group['id'])['last_error']
        self.assertIn('所选课程当前没有匹配账号：Missing course',error)

    async def test_asset_location_and_auto_group_api(self):
        gateway=SimpleNamespace(
            groups=AsyncMock(return_value=[]),create_group=AsyncMock(),revoke_group=AsyncMock(),
            settings=Mock(return_value={'base_url':'https://sign.example','configured':True,'admin_secret_configured':True}),
        )
        app=FastAPI(); app.include_router(attendance_router(self.service,gateway))
        png=b'\x89PNG\r\n\x1a\n'+b'test'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owner') as client:
            uploaded=await client.post(
                '/api/attendance/accounts/'+self.accounts[0]['id']+'/assets/photo',
                files={'file':('photo.png',png,'image/png')},
            )
            self.assertEqual(uploaded.status_code,200,uploaded.text)
            self.assertTrue(uploaded.json()['photo'])
            self.assertEqual((await client.get('/api/attendance/accounts/'+self.accounts[0]['id']+'/assets/photo')).status_code,200)
            location=await client.put('/api/attendance/location',json={'latitude':30.5,'longitude':114.3,'address':'教学楼'})
            self.assertEqual(location.status_code,200,location.text)
            courses=await client.post('/api/attendance/auto-groups/courses',json={
                'account_ids':[self.accounts[0]['id']],
            })
            self.assertEqual(courses.status_code,200,courses.text)
            self.assertEqual(courses.json()['courses'][0]['name'],'Test classroom')
            created=await client.post('/api/attendance/auto-groups',json={
                'name':'自动组','account_ids':[self.accounts[0]['id']],'kinds':['normal','code'],
                'course_filters':['计算机科学','软件工程'],
                'windows':[{'days':[1,2,3,4,5],'start':'08:00','end':'18:00'}],
                'interval_seconds':60,'enabled':True,'location':None,
            })
            self.assertEqual(created.status_code,200,created.text)
            self.assertEqual(created.json()['course_filters'],['计算机科学','软件工程'])
            self.assertEqual(created.json()['windows'],[{'days':[1,2,3,4,5],'start':'08:00','end':'18:00'}])
            self.assertEqual(created.json()['account_budgets'][self.accounts[0]['id']]['scan_remaining'],2400)
            self.assertIn('class_scan',created.json())
            self.assertIn(created.json()['scan_phase'],{'normal','prewarm','burst','outside'})
            self.assertGreaterEqual(created.json()['effective_interval_seconds'],3)
            group_id=created.json()['id']
            scanned=await client.post('/api/attendance/auto-groups/'+group_id+'/scan')
            self.assertEqual(scanned.status_code,200,scanned.text)
            self.assertEqual(scanned.json()['id'],group_id)
            updated=await client.patch('/api/attendance/auto-groups/'+group_id,json={'enabled':False})
            self.assertEqual(updated.status_code,200,updated.text); self.assertFalse(updated.json()['enabled'])
            relay=await client.get('/api/attendance/relay')
            self.assertTrue(relay.json()['configured'])
            self.assertEqual(relay.json()['mode'],'custom')
            self.assertNotIn('txt_value',relay.json())
            self.assertNotIn('cname_target',relay.json())
            self.assertNotIn('multi_domain_ready',relay.json())
            self.assertNotIn('admin_secret',relay.json())
            self.assertEqual((await client.delete('/api/attendance/auto-groups/'+group_id)).status_code,200)

    async def test_relay_domain_settings_are_tested_before_saving(self):
        state={'base_url':'','configured':False,'admin_secret_configured':False}
        gateway=SimpleNamespace(
            groups=AsyncMock(return_value=[]),create_group=AsyncMock(),revoke_group=AsyncMock(),
            base_url='',admin_secret='',settings=lambda:state,configure=AsyncMock(),ensure_started=AsyncMock(),
        )
        app=FastAPI(); app.include_router(attendance_router(self.service,gateway))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owner') as client:
            with patch('app.services.attendance.relay.RelayClient.health',AsyncMock(return_value={'ok':True})), \
                 patch('app.services.attendance.relay.RelayClient.close',AsyncMock(return_value=None)):
                response=await client.put('/api/attendance/relay',json={
                    'base_url':'https://sign.example.com','admin_secret':'s'*32,
                })
        self.assertEqual(response.status_code,200,response.text)
        saved=self.db.get_setting('attendance_relay')
        self.assertEqual(saved['mode'],'custom')
        self.assertEqual(saved['base_url'],'https://sign.example.com')
        self.assertEqual(saved['admin_secret'],'s'*32)
        gateway.configure.assert_awaited_once_with(
            'https://sign.example.com','s'*32,mode='custom',official_admin_secret='',
        )

    async def test_official_relay_uses_backend_secret_without_exposing_it(self):
        secret='o'*32
        state={'base_url':OFFICIAL_RELAY_BASE_URL,'mode':'official','official':True,'configured':True,'admin_secret_configured':True}
        gateway=SimpleNamespace(
            groups=AsyncMock(return_value=[]),create_group=AsyncMock(),revoke_group=AsyncMock(),
            base_url=OFFICIAL_RELAY_BASE_URL,admin_secret=secret,mode='official',
            official_admin_secret=secret,settings=lambda:state,configure=AsyncMock(),ensure_started=AsyncMock(),
        )
        app=FastAPI(); app.include_router(attendance_router(self.service,gateway))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owner') as client:
            current=await client.get('/api/attendance/relay')
            self.assertEqual(current.status_code,200,current.text)
            self.assertEqual(current.json()['mode'],'official')
            self.assertTrue(current.json()['official'])
            self.assertNotIn('admin_secret',current.json())
            self.assertNotIn(secret,current.text)
            with patch('app.services.attendance.relay.RelayClient.health',AsyncMock(return_value={'ok':True})), \
                 patch('app.services.attendance.relay.RelayClient.close',AsyncMock(return_value=None)):
                response=await client.put('/api/attendance/relay',json={'mode':'official'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn(secret,response.text)
        saved=self.db.get_setting('attendance_relay')
        self.assertEqual(saved['mode'],'official')
        self.assertEqual(saved['base_url'],OFFICIAL_RELAY_BASE_URL)
        self.assertEqual(saved['admin_secret'],secret)
        gateway.configure.assert_awaited_once_with(
            OFFICIAL_RELAY_BASE_URL,secret,mode='official',official_admin_secret=secret,
        )

    async def test_cancellation_preserves_unconfirmed_submit(self):
        entered=asyncio.Event()
        original=self.service.client_factory
        class Blocking(original):
            async def submit(self,prepared): entered.set(); await asyncio.Event().wait()
        self.service.client_factory=Blocking
        batch=self.batch()
        self.service.start(batch['id'],SignInput()); await asyncio.wait_for(entered.wait(),2)
        self.assertTrue(self.service.active(self.accounts[0]['id']))
        result=await self.service.cancel(batch['id'])
        self.assertIn('unknown',[item['status'] for item in result['items']])
        self.assertFalse(self.service.active(self.accounts[0]['id']))

    async def test_complete_collection_removes_stale_courses_but_partial_failure_retains_records(self):
        self.service.store.save_activities(self.accounts[0]['id'],[],[],[('101','202')])
        self.assertEqual(len(self.service.store.activities([self.accounts[0]['id']])),1)
        self.service.store.save_activities(self.accounts[0]['id'],[],[],[])
        self.assertEqual(len(self.service.store.activities([self.accounts[0]['id']])),0)

    async def test_auto_scan_skips_activities_waiting_for_user_input(self):
        batch=self.batch()
        item=next(
            item for item in batch['items']
            if item['account_id']==self.accounts[0]['id']
        )
        self.service.store.update_item(item['id'],'awaiting_input','请配置照片素材')
        self.assertEqual(len(self.service.store.activities([self.accounts[0]['id']])),1)
        self.assertEqual(
            len(self.service.store.activities([self.accounts[0]['id']],auto_only=True)),
            0,
        )

    async def test_updated_material_allows_one_automatic_retry(self):
        batch=self.batch()
        item=next(
            item for item in batch['items']
            if item['account_id']==self.accounts[0]['id']
        )
        self.service.store.update_item(item['id'],'awaiting_input','请配置照片素材')
        self.assertEqual(
            len(self.service.store.activities([self.accounts[0]['id']],auto_only=True)),
            0,
        )
        assets=self.service.store.save_account_asset(
            self.accounts[0]['id'],
            'photo',
            Path(self.temp.name)/'attendance-assets'/'updated-photo.png',
            'image/png',
        )
        retried=self.service.store.activities(
            [self.accounts[0]['id']],
            auto_only=True,
            retry_after=assets['updated_at'],
        )
        self.assertEqual(len(retried),1)

    async def test_manual_auto_scan_reconciles_unknown_results_before_retry(self):
        group=self.service.store.create_auto_group(
            '恢复核对组',
            [self.accounts[0]['id']],
            ['normal'],
            60,
            True,
            None,
        )
        batch=self.batch()
        self.service.store.mark_auto_group_scan(group['id'],batch_id=batch['id'],error='')
        self.service.store.update_item(batch['items'][0]['id'],'unknown','未确认回执')
        self.service.reconcile=AsyncMock(return_value=self.service.store.batch(batch['id']))
        self.service._auto_group=AsyncMock()
        await self.service.scan_auto_group_now(group['id'])
        self.service.reconcile.assert_awaited_once_with(batch['id'])
        self.service._auto_group.assert_awaited_once()

    async def test_batch_creation_rejects_unowned_invalid_and_duplicate_records(self):
        for ids in (['invented'],[self.rows[0]['id'],self.rows[0]['id']],[]):
            with self.assertRaises(AttendanceError): self.service.store.create_batch(ids)
        self.db.update_account(self.accounts[0]['id'],status='new')
        with self.assertRaises(AttendanceError): self.service.store.create_batch([self.rows[0]['id']])

    async def test_owner_api_creates_batches(self):
        gateway=SimpleNamespace()
        app=FastAPI(); app.include_router(attendance_router(self.service,gateway))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owner') as client:
            self.assertEqual((await client.get('/api/attendance/activities')).status_code,404)
            self.assertEqual((await client.post('/api/attendance/accounts/'+self.accounts[0]['id']+'/collect')).status_code,404)
            self.assertEqual((await client.post('/api/attendance/qr-sessions',json={})).status_code,404)
            self.assertEqual((await client.delete('/api/attendance/shares/legacy')).status_code,404)
            response=await client.post('/api/attendance/batches',json={'activity_ids':[self.rows[0]['id']]})
            self.assertEqual(response.status_code,200,response.text); identifier=response.json()['id']
            self.assertEqual((await client.post('/api/attendance/batches/'+identifier+'/actions/execute',json={})).status_code,200)
            await self.finished(identifier)
            self.assertEqual((await client.get('/api/attendance/batches/'+identifier)).json()['completed'],1)

    async def test_owner_api_creates_named_long_link_with_account_scope(self):
        gateway=SimpleNamespace(
            groups=AsyncMock(return_value=[]),
            create_group=AsyncMock(return_value={'id':'local-group','name':'计算机科学 1 组','url':'https://xxt.example/qr/#token'}),
            revoke_group=AsyncMock(),
        )
        app=FastAPI(); app.include_router(attendance_router(self.service,gateway))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owner') as client:
            self.assertEqual((await client.get('/api/attendance/qr-groups')).status_code,200)
            response=await client.post('/api/attendance/qr-groups',json={
                'name':'计算机科学 1 组','account_ids':[account['id'] for account in self.accounts],'pin':'2468','base_url':'',
            })
            self.assertEqual(response.status_code,200,response.text)
            gateway.create_group.assert_awaited_once()
            args=gateway.create_group.await_args.args
            self.assertEqual(args[0],'计算机科学 1 组')
            self.assertEqual(args[1],[account['id'] for account in self.accounts])
            self.assertEqual((await client.delete('/api/attendance/qr-groups/local-group')).status_code,200)

    async def test_remote_gateway_encrypts_long_link_and_never_sends_account_ids(self):
        account_ids=[account['id'] for account in self.accounts]
        gateway=RemoteShareGateway(self.service,'https://share.example','s'*32)
        gateway.client=SimpleNamespace(
            create_group=AsyncMock(return_value={
                'id':'remote-group','token':'secret-token','url':'https://share.example/qr/#secret-token',
                'requires_pin':True,'persistent':True,
            }),
            revoke_group=AsyncMock(),
        )
        with patch.object(gateway,'_monitor_group',new=AsyncMock(return_value=None)):
            created=await gateway.create_group('计算机科学 1 组',account_ids,pin='2468')
            groups=await gateway.groups()
        payload=gateway.client.create_group.await_args.args[0]
        self.assertEqual(payload['name'],'计算机科学 1 组')
        self.assertEqual(payload['pin'],'2468')
        self.assertNotIn(account_ids[0],str(payload)); self.assertNotIn(account_ids[1],str(payload))
        self.assertEqual(created['url'],'https://share.example/qr/#secret-token')
        self.assertEqual(groups[0]['url'],'https://share.example/qr/#secret-token')
        with self.db.connect() as db:
            raw=db.execute('SELECT link_ciphertext FROM attendance_qr_groups WHERE id=?',(created['id'],)).fetchone()[0]
        self.assertNotIn('secret-token',raw)

    async def test_client_uses_platform_receipts_instead_of_default_detail_flags(self):
        client=AttendanceClient({'name':'Student','platform_user_id':'1000'})
        client.activities=AsyncMock(return_value=[activity()])
        for flags in (
            {'otherId':0},
            {'otherId':0,'showVCode':1,'ifNeedVCode':1,'openPreventCheatFlag':1},
        ):
            client.json=AsyncMock(side_effect=[{'result':1,'data':flags},{'result':1,'data':{'status':0}}])
            self.assertEqual((await client.prepare(activity(),SignInput()))['status'],'ready')
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{'otherId':2,'openCheckFaceFlag':True}},
            {'result':1,'data':{'status':0}},
        ])
        face=await client.prepare(activity(kind='qr'),SignInput())
        self.assertEqual(face['status'],'manual_required')
        self.assertIn('人脸',face['message'])
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{'otherId':2,'locationText':'Test place','locationRange':50}},
            {'result':1,'data':{'status':0}},
        ])
        location=await client.prepare(activity(kind='qr'),SignInput())
        self.assertEqual(location['status'],'manual_required')
        self.assertIn('位置',location['message'])
        client.activities=AsyncMock(return_value=[])
        client.json=AsyncMock(return_value={'result':1,'data':{'status':0}})
        self.assertEqual((await client.prepare(activity(),SignInput()))['status'],'expired')
        client.json=AsyncMock(return_value={'result':1,'data':{'status':1}})
        self.assertEqual((await client.prepare(activity(),SignInput()))['status'],'already_signed')

    async def test_location_requires_explicit_input_and_never_copies_teacher_coordinates(self):
        client=AttendanceClient({'name':'Student','platform_user_id':'1000'})
        client.activities=AsyncMock(return_value=[activity(kind='location')])
        details={'otherId':4,'locationText':'Teacher location','locationRange':50,
                 'locationLatitude':39.9,'locationLongitude':116.4,'ifopenAddress':1}
        def replies():
            client.json=AsyncMock(side_effect=[{'result':1,'data':details},{'result':1,'data':{'status':0}}])
        replies()
        missing=await client.prepare(activity(kind='location'),SignInput())
        self.assertEqual(missing['status'],'awaiting_input'); self.assertNotIn('params',missing)
        inputs=SignInput(location={'latitude':30.5,'longitude':114.3,'address':'Actual classroom','source':'browser'})
        replies()
        prepared=await client.prepare(activity(kind='location'),inputs)
        self.assertEqual(prepared['status'],'ready')
        expected_longitude,expected_latitude=wgs84_to_bd09(114.3,30.5)
        expected_longitude=round(expected_longitude,6); expected_latitude=round(expected_latitude,6)
        self.assertEqual(float(prepared['params']['latitude']),expected_latitude)
        self.assertEqual(float(prepared['params']['longitude']),expected_longitude)
        self.assertEqual(prepared['params']['address'],'Actual classroom'); self.assertEqual(prepared['params']['ifTiJiao'],'1')
        location_payload=json.loads(prepared['params']['location'])
        location_result=json.loads(prepared['params']['locationResult'])
        self.assertEqual(location_payload['latitude'],expected_latitude)
        self.assertEqual(location_payload['longitude'],expected_longitude)
        self.assertEqual(location_result['latitude'],expected_latitude)
        self.assertEqual(location_result['longitude'],expected_longitude)
        self.assertEqual(location_result['mockData'],'{"strategy":0,"probability":-1}')
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{**details,'openLocationFlag':1}},
            {'result':1,'data':{'status':0}},
        ])
        self.assertEqual((await client.prepare(activity(kind='location'),inputs))['status'],'ready')
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{**details,'openCheckFaceFlag':1}},
            {'result':1,'data':{'status':0}},
        ])
        self.assertEqual((await client.prepare(activity(kind='location'),inputs))['status'],'manual_required')

    async def test_gesture_and_code_are_resolved_and_checked_without_user_input(self):
        client=AttendanceClient({'name':'Student','platform_user_id':'1000'})
        client.activities=AsyncMock(return_value=[activity(kind='gesture')])
        stored={}
        client.codebook_get=lambda aid: None
        client.codebook_put=lambda aid,code,source,kind: stored.update({'active_id':aid,'code':code,'source':source,'kind':kind})
        code='AilJpeOmQAA6CCTI81DWdg=='
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{'otherId':3}},
            {'result':1,'data':{'status':0}},
            {'result':1,'data':{'signCode':code}},
            {'result':1,'msg':''},
        ])
        prepared=await client.prepare(activity(kind='gesture'),SignInput())
        self.assertEqual(prepared['status'],'ready')
        self.assertEqual(prepared['params']['signCode'],code)
        self.assertEqual(prepared['code_source'],'activity')
        self.assertEqual(stored['code'],code)

    async def test_photo_sign_uploads_the_account_photo_asset(self):
        client=AttendanceClient({'name':'Student','platform_user_id':'1000','attendance_photo_path':'photo.png'})
        client.activities=AsyncMock(return_value=[activity(kind='photo')])
        client.upload_photo=AsyncMock(return_value='object-photo')
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{'otherId':1,'ifPhoto':1}},
            {'result':1,'data':{'status':0}},
        ])
        prepared=await client.prepare(activity(kind='photo'),SignInput())
        self.assertEqual(prepared['status'],'ready')
        self.assertEqual(prepared['params']['objectId'],'object-photo')
        self.assertEqual(prepared['params']['ifPhoto'],'1')
        client.upload_photo.assert_awaited_once_with('photo.png')

    async def test_normal_sign_with_photo_gate_uploads_the_account_photo_asset(self):
        client=AttendanceClient({'name':'Student','platform_user_id':'1000','attendance_photo_path':'photo.png'})
        client.activities=AsyncMock(return_value=[activity(kind='normal')])
        client.upload_photo=AsyncMock(return_value='object-photo')
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{'otherId':0,'ifphoto':1}},
            {'result':1,'data':{'status':0}},
        ])
        prepared=await client.prepare(activity(kind='normal'),SignInput())
        self.assertEqual(prepared['status'],'ready')
        self.assertEqual(prepared['params']['objectId'],'object-photo')
        self.assertEqual(prepared['params']['ifPhoto'],'1')
        client.upload_photo.assert_awaited_once_with('photo.png')

    async def test_normal_sign_with_photo_gate_requires_a_photo_asset(self):
        client=AttendanceClient({'name':'Student','platform_user_id':'1000'})
        client.activities=AsyncMock(return_value=[activity(kind='normal')])
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{'otherId':0,'ifphoto':1}},
            {'result':1,'data':{'status':0}},
        ])
        prepared=await client.prepare(activity(kind='normal'),SignInput())
        self.assertEqual(prepared['status'],'awaiting_input')
        self.assertIn('拍照',prepared['message'])

    async def test_live_photo_credential_is_manual_even_when_a_photo_asset_exists(self):
        client=AttendanceClient({'name':'Student','platform_user_id':'1000','attendance_photo_path':'photo.png'})
        client.activities=AsyncMock(return_value=[activity(kind='normal')])
        client.upload_photo=AsyncMock()
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{'otherId':0,'ifphoto':1,'openPreventCheatFlag':1}},
            {'result':1,'data':{'status':0}},
        ])
        prepared=await client.prepare(activity(kind='normal'),SignInput())
        self.assertEqual(prepared['status'],'manual_required')
        self.assertIn('现场拍摄',prepared['message'])
        self.assertIn('不是人脸',prepared['message'])
        client.upload_photo.assert_not_awaited()

    async def test_face_asset_is_not_reused_as_a_photo_sign_material(self):
        client=AttendanceClient({'name':'Student','platform_user_id':'1000','attendance_face_path':'face.png'})
        client.activities=AsyncMock(return_value=[activity(kind='photo')])
        client.upload_photo=AsyncMock()
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{'otherId':1,'ifPhoto':1}},
            {'result':1,'data':{'status':0}},
        ])
        prepared=await client.prepare(activity(kind='photo'),SignInput())
        self.assertEqual(prepared['status'],'awaiting_input')
        self.assertIn('拍照签到素材',prepared['message'])
        client.upload_photo.assert_not_awaited()

    async def test_short_lived_qr_can_resolve_activity_and_prepare_without_full_scan(self):
        client=AttendanceClient({'name':'Student','platform_user_id':'1000'},fast=True)
        client.courses=AsyncMock(return_value=[course()])
        now=time.time()
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{'id':303,'name':'QR','otherId':2,'clazzid':202,'status':1,
                                'startTime':now-30,'endTime':now+600}},
        ])
        resolved=await client.quick_activity('303')
        self.assertEqual(resolved['course_id'],'101')
        prepared=await client.prepare(
            {**resolved,'upstream_id':'303','status':'queued'},
            SignInput(qr='https://mobilelearn.chaoxing.com/widget/sign/e?id=303&enc=abcdefgh1234'),
            fast=True,
        )
        self.assertEqual(prepared['status'],'ready')
        self.assertEqual(prepared['params']['enc'],'abcdefgh1234')
        self.assertEqual(client.json.await_count,1)

    async def test_relay_detects_platform_expired_qr_as_retryable(self):
        self.assertTrue(RemoteShareGateway._needs_qr_rescan({
            'status':'awaiting_input',
            'items':[{'kind':'qr','status':'awaiting_input','message':'二维码无效或已过期，请重新扫码'}],
        }))
        self.assertFalse(RemoteShareGateway._needs_qr_rescan({
            'status':'awaiting_input',
            'items':[{'kind':'qr','status':'awaiting_input','message':'请提供当前有效的签到二维码内容'}],
        }))

    async def test_relay_client_reuses_one_keepalive_connection_and_closes_it(self):
        response=SimpleNamespace(status_code=200,json=lambda:{'ok':True},text='')
        http=SimpleNamespace(
            is_closed=False,get=AsyncMock(return_value=response),post=AsyncMock(return_value=response),
            aclose=AsyncMock(),
        )
        with patch('app.services.attendance.relay.httpx.AsyncClient',return_value=http) as factory:
            client=RelayClient('https://share.example','s'*32)
            await client.get('one')
            await client.get('two')
            await client.close()
        self.assertEqual(factory.call_count,1)
        self.assertEqual(http.get.await_count,2)
        http.aclose.assert_awaited_once()

    async def test_relay_client_uses_bulk_group_statuses(self):
        response=SimpleNamespace(
            status_code=200,
            json=lambda:{'groups':[
                {'id':'one','status':'waiting','run_id':None,'capture_text':'','can_capture':True},
                {'id':'two','status':'signing','run_id':'run-two','capture_text':'','can_capture':False},
            ]},
            text='',
        )
        http=SimpleNamespace(
            is_closed=False,
            get=AsyncMock(return_value=response),
            aclose=AsyncMock(),
        )
        with patch('app.services.attendance.relay.httpx.AsyncClient',return_value=http):
            client=RelayClient('https://share.example','s'*32)
            groups=await client.groups(['one','one','two'])
            await client.close()
        self.assertEqual(set(groups),{'one','two'})
        http.get.assert_awaited_once_with(
            'https://share.example/api/admin/groups',
            params={'ids':'one,two'},
            headers={'X-Mumu-Share-Secret':'s'*32},
        )

    async def test_avatar_snapshot_is_small_webp_with_full_names(self):
        profile=Path(self.temp.name)/'avatar-account'; profile.mkdir()
        source=Image.new('RGB',(180,120),(30,120,210))
        ImageDraw.Draw(source).ellipse((45,20,135,100),fill=(250,220,90))
        source.save(profile/'avatar.bin','JPEG')
        avatar=_avatar_data({
            **self.accounts[0],
            'profile_dir':str(profile),
            'avatar_mime':'image/jpeg',
        })
        self.assertTrue(avatar.startswith('data:image/webp;base64,'))
        image=Image.open(BytesIO(base64.b64decode(avatar.split(',',1)[1])))
        self.assertEqual(image.size,(96,96))

        store=RelayStore(Path(self.temp.name)/'avatar-relay.sqlite3','x'*32)
        group=store.create_group(
            name='计算机科学',
            items=[{
                'id':self.accounts[0]['id'],
                'name':'郭一铭',
                'avatar':avatar,
                'course':'计算机科学',
            }],
            base_url='https://xxt.example',
        )
        waiting=store.public_group(store.group_by_id(group['id']))
        self.assertEqual(waiting['items'][0]['account_id'],self.accounts[0]['id'])
        self.assertEqual(waiting['items'][0]['name'],'郭一铭')
        self.assertEqual(waiting['items'][0]['avatar'],avatar)

    async def test_avatar_snapshot_ignores_platform_placeholder_images(self):
        profile=Path(self.temp.name)/'placeholder-avatar'; profile.mkdir()
        Image.new('RGB',(80,80),(12,12,12)).save(profile/'avatar.bin','PNG')
        self.assertEqual(_avatar_data({
            **self.accounts[0],
            'profile_dir':str(profile),
            'avatar_mime':'image/png',
        }),'')

    async def test_relay_group_metadata_can_refresh_existing_link_identity(self):
        directory=tempfile.TemporaryDirectory()
        path=Path(directory.name)/'relay.sqlite3'
        store=RelayStore(path,'x'*32)
        app=relay_app(store,'s'*32)
        admin={'X-Mumu-Share-Secret':'s'*32}
        old=os.environ.get('MUMU_PUBLIC_BASE_URL')
        os.environ['MUMU_PUBLIC_BASE_URL']='https://sign.example'
        try:
            created=store.create_group(
                name='计算机科学',
                items=[{'id':'one','name':'旧姓名','course':'计算机科学'}],
                base_url='https://sign.example',
            )
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://relay') as client:
                updated=await client.patch(
                    '/api/admin/groups/'+created['id']+'/metadata',
                    headers=admin,
                    json={'items':[{
                        'id':'one','name':'郭一铭','avatar':'data:image/webp;base64,AAAA',
                        'course':'计算机科学',
                    }]},
                )
                self.assertEqual(updated.status_code,200,updated.text)
                token=created['url'].split('#',1)[1]
                waiting=(await client.get(
                    '/api/qr/session',
                    headers={'Authorization':'Bearer '+token},
                )).json()
            self.assertEqual(waiting['items'][0]['name'],'郭一铭')
            self.assertEqual(
                waiting['items'][0]['avatar'],
                'data:image/webp;base64,AAAA',
            )
        finally:
            if old is None: os.environ.pop('MUMU_PUBLIC_BASE_URL',None)
            else: os.environ['MUMU_PUBLIC_BASE_URL']=old
            directory.cleanup()

    async def test_remote_gateway_shares_one_bulk_group_poll_between_monitors(self):
        records=[
            {'remote_group_id':'one'},
            {'remote_group_id':'two'},
        ]
        store=SimpleNamespace(live_qr_groups=Mock(return_value=records))
        gateway=RemoteShareGateway(
            SimpleNamespace(store=store),
            'https://share.example',
            's'*32,
        )
        gateway.client=SimpleNamespace(groups=AsyncMock(return_value={
            'one':{'id':'one','status':'waiting'},
            'two':{'id':'two','status':'waiting'},
        }))
        one,two=await asyncio.gather(
            gateway._polled_group('one'),
            gateway._polled_group('two'),
        )
        self.assertEqual((one['id'],two['id']),('one','two'))
        gateway.client.groups.assert_awaited_once_with(['one','two'])

    async def test_unconfigured_remote_gateway_closes_without_a_client(self):
        gateway=RemoteShareGateway(self.service,'','')
        self.assertIsNone(gateway.client)
        await gateway.close()

    async def test_remote_monitor_does_not_send_redundant_intermediate_status(self):
        item={'account_name':'Alice','course_name':'Course','title':'QR','kind':'qr','status':'succeeded','message':'ok'}
        service=SimpleNamespace(
            store=SimpleNamespace(batch=Mock(return_value={
                'status':'completed','completed':1,'total':1,'items':[item],
            })),
            start=Mock(),
        )
        gateway=RemoteShareGateway(service,'https://share.example','s'*32)
        gateway.client=SimpleNamespace(
            get=AsyncMock(return_value={
                'capture_text':'https://mobilelearn.chaoxing.com/widget/sign/e?id=1&enc=abcdefgh1234',
                'status':'captured','revoked':False,'expires_at':time.time()+60,
            }),
            ack=AsyncMock(),status=AsyncMock(),reopen=AsyncMock(),
        )
        await gateway._monitor('remote','batch','activity')
        gateway.client.status.assert_awaited_once()
        self.assertEqual(gateway.client.status.await_args.args[1]['status'],'completed')
        gateway.client.ack.assert_awaited_once()
        gateway.client.reopen.assert_not_awaited()

    async def test_remote_monitor_reopens_failed_qr_without_an_extra_ack(self):
        item={'account_name':'Alice','course_name':'Course','title':'QR','kind':'qr','status':'awaiting_input','message':'二维码无效或已过期，请重新扫码'}
        service=SimpleNamespace(
            store=SimpleNamespace(batch=Mock(return_value={
                'status':'awaiting_input','completed':0,'total':1,'items':[item],
            })),
            start=Mock(),
        )
        gateway=RemoteShareGateway(service,'https://share.example','s'*32)
        gateway.client=SimpleNamespace(
            get=AsyncMock(side_effect=[
                {
                    'capture_text':'https://mobilelearn.chaoxing.com/widget/sign/e?id=1&enc=abcdefgh1234',
                    'status':'captured','revoked':False,'expires_at':time.time()+60,
                },
                {'capture_text':'','status':'expired','revoked':False,'expires_at':time.time()+60},
            ]),
            ack=AsyncMock(),status=AsyncMock(),reopen=AsyncMock(),
        )
        with patch('app.services.attendance.relay.asyncio.sleep',AsyncMock()):
            await gateway._monitor('remote','batch','activity')
        gateway.client.reopen.assert_awaited_once()
        gateway.client.ack.assert_not_awaited()
        gateway.client.status.assert_not_awaited()

    async def test_long_link_monitor_creates_a_new_batch_and_starts_next_scan(self):
        account_ids=[self.accounts[0]['id']]
        waiting={
            'id':'local-group','name':'计算机科学 1 组','revoked':False,'last_status':'waiting',
            'last_remote_run_id':None,'last_batch_id':None,
        }
        running={**waiting,'last_status':'captured','last_remote_run_id':'run-one','last_batch_id':'batch-one'}
        empty={'id':'batch-one','status':'finished','items':[],'completed':0,'total':0,'participants':[]}
        active_item={'account_id':account_ids[0],'account_name':'Alice','course_name':'Course','title':'QR','kind':'qr','status':'queued','message':'waiting'}
        running_batch={'id':'batch-one','status':'running','items':[active_item],'completed':0,'total':1}
        completed_batch={'id':'batch-one','status':'completed','items':[{**active_item,'status':'succeeded','message':'ok'}],'completed':1,'total':1}
        store=SimpleNamespace(
            qr_group=Mock(side_effect=[waiting,running,running,running]),
            create_open_qr_batch=Mock(return_value={'id':'batch-one'}),
            set_qr_group_run=Mock(),set_qr_group_status=Mock(),
            batch=Mock(side_effect=[empty,running_batch,completed_batch]),
        )
        service=SimpleNamespace(
            store=store,resolve_open_qr_batch=AsyncMock(return_value=running_batch),start=Mock(),
        )
        gateway=RemoteShareGateway(service,'https://share.example','s'*32)
        gateway.client=SimpleNamespace(
            group=AsyncMock(side_effect=[
                {'status':'captured','run_id':'run-one','capture_text':'https://mobilelearn.chaoxing.com/widget/sign/e?id=1&enc=abcdefgh1234'},
                {'status':'captured','run_id':'run-one','capture_text':'https://mobilelearn.chaoxing.com/widget/sign/e?id=1&enc=abcdefgh1234'},
                {'status':'revoked','run_id':'run-one','capture_text':''},
            ]),
            group_status=AsyncMock(),
        )
        with patch('app.services.attendance.relay.asyncio.sleep',AsyncMock()):
            await gateway._monitor_group('local-group','remote-group',account_ids)
        store.create_open_qr_batch.assert_called_once_with(account_ids)
        service.resolve_open_qr_batch.assert_awaited_once()
        service.start.assert_called_once()
        gateway.client.group_status.assert_awaited_once()
        self.assertEqual(gateway.client.group_status.await_args.args[2]['status'],'completed')

    async def test_idle_long_link_monitor_uses_the_slow_idle_poll(self):
        local={'id':'local-group','revoked':False,'last_status':'waiting','last_remote_run_id':None,'last_batch_id':None}
        store=SimpleNamespace(
            qr_group=Mock(return_value=local),
            set_qr_group_status=Mock(),
        )
        gateway=RemoteShareGateway(SimpleNamespace(store=store),'https://share.example','s'*32)
        gateway.client=SimpleNamespace(group=AsyncMock(return_value={'status':'waiting','run_id':None}))
        sleep=AsyncMock(side_effect=asyncio.CancelledError)
        with patch('app.services.attendance.relay.asyncio.sleep',sleep):
            with self.assertRaises(asyncio.CancelledError):
                await gateway._monitor_group('local-group','remote-group',[])
        self.assertEqual(sleep.await_args.args[0],GROUP_IDLE_POLL_SECONDS)

    async def test_long_link_monitor_marks_offline_and_recovers(self):
        state={'local':{'id':'local-group','revoked':False,'last_status':'waiting','last_remote_run_id':None,'last_batch_id':None}}
        statuses=[]
        def qr_group(_):
            return dict(state['local'])
        def set_status(_,status):
            statuses.append(status)
            state['local']['last_status']=status
        store=SimpleNamespace(qr_group=Mock(side_effect=qr_group),set_qr_group_status=Mock(side_effect=set_status))
        gateway=RemoteShareGateway(SimpleNamespace(store=store),'https://share.example','s'*32)
        gateway.client=SimpleNamespace(group=AsyncMock(side_effect=[
            ConnectionError('first outage'),
            ConnectionError('second outage'),
            {'status':'revoked','run_id':None},
        ]))
        with patch('app.services.attendance.relay.asyncio.sleep',AsyncMock()):
            await gateway._monitor_group('local-group','remote-group',[])
        self.assertEqual(statuses[:3],['offline','waiting','revoked'])

    async def test_open_qr_link_matches_activity_by_active_id_and_starts_all_accounts(self):
        for account in self.accounts:
            self.service.store.save_activities(account['id'],[activity('505','qr')],[('101','202')])
        account_ids=[account['id'] for account in self.accounts]
        batch=self.service.store.create_open_qr_batch(account_ids)
        self.assertEqual(batch['status'],'finished')
        text='https://mobilelearn.chaoxing.com/widget/sign/e?id=505&enc=abcdefgh1234'
        await self.service.resolve_open_qr_batch(batch['id'],text,account_ids)
        self.service.start(batch['id'],SignInput(qr=text),source='long_link')
        await self.finished(batch['id'])
        self.assertEqual(len(self.submissions),2)
        self.assertTrue(all(item['kind']=='qr' for item in self.service.store.batch(batch['id'])['items']))

    async def test_open_qr_link_runs_matched_accounts_and_reports_the_unmatched_account(self):
        for account in self.accounts:
            self.service.store.save_activities(account['id'],[activity('606','qr')],[('101','202')])
        outsider=self.db.create_account('Outsider','chaoxing',self.settings)
        self.db.update_account(outsider['id'],status='ready',platform_user_id='9999')
        account_ids=[account['id'] for account in self.accounts]+[outsider['id']]
        batch=self.service.store.create_open_qr_batch(account_ids)
        self.service.collect=AsyncMock()
        text='https://mobilelearn.chaoxing.com/widget/sign/e?id=606&enc=abcdefgh1234'
        matched=await self.service.resolve_open_qr_batch(batch['id'],text,account_ids)
        self.assertCountEqual(
            [item['account_id'] for item in matched['items']],
            [account['id'] for account in self.accounts],
        )
        self.service.start(batch['id'],SignInput(qr=text),source='long_link')
        result=await self.finished(batch['id'])
        self.assertEqual(result['completed'],2)
        self.assertEqual(len(self.submissions),2)

        gateway=RemoteShareGateway(self.service,'https://share.example','s'*32)
        public=gateway._public(result,'open',account_ids)
        self.assertEqual(public['status'],'partial')
        self.assertEqual(public['summary'],{'completed':2,'total':3})
        unmatched=next(item for item in public['items'] if item['course']=='未匹配到活动')
        self.assertEqual(unmatched['status'],'failed')
        self.assertIn('不在当前二维码对应的班级中',unmatched['message'])

        share=public_share_batch(self.service,{
            'batch_id':result['id'],'mode':'open','account_ids_json':json.dumps(account_ids),
        })
        self.assertEqual(share['status'],'partial')
        self.assertEqual(share['total'],3)

    async def test_open_qr_link_rejects_the_batch_only_when_no_account_matches(self):
        account_ids=[account['id'] for account in self.accounts]
        batch=self.service.store.create_open_qr_batch(account_ids)
        self.service.collect=AsyncMock()
        with self.assertRaises(AttendanceError):
            await self.service.resolve_open_qr_batch(
                batch['id'],
                'https://mobilelearn.chaoxing.com/widget/sign/e?id=999&enc=abcdefgh1234',
                account_ids,
            )
        self.assertEqual(self.service.store.batch(batch['id'])['items'],[])

    async def test_open_qr_link_reuses_matched_batch_after_expired_enc_rescan(self):
        for account in self.accounts:
            self.service.store.save_activities(account['id'],[activity('707','qr')],[('101','202')])
        account_ids=[account['id'] for account in self.accounts]
        batch=self.service.store.create_open_qr_batch(account_ids)
        first='https://mobilelearn.chaoxing.com/widget/sign/e?id=707&enc=oldcode1234'
        matched=await self.service.resolve_open_qr_batch(batch['id'],first,account_ids)
        item_ids=[item['id'] for item in matched['items']]
        for item in matched['items']:
            self.service.store.update_item(item['id'],'awaiting_input','二维码无效或已过期，请重新扫码')
        fresh='https://mobilelearn.chaoxing.com/widget/sign/e?id=707&enc=freshcode1234'
        refreshed=await self.service.resolve_open_qr_batch(batch['id'],fresh,account_ids)
        self.assertEqual([item['id'] for item in refreshed['items']],item_ids)
        with self.assertRaises(AttendanceError):
            await self.service.resolve_open_qr_batch(
                batch['id'],
                'https://mobilelearn.chaoxing.com/widget/sign/e?id=708&enc=othercode1234',
                account_ids,
            )
        self.service.start(batch['id'],SignInput(qr=fresh))
        result=await self.finished(batch['id'])
        self.assertEqual(result['completed'],2)
        self.assertEqual(len(self.submissions),2)

    async def test_remote_relay_group_supports_repeated_runs_until_revoked(self):
        directory=tempfile.TemporaryDirectory()
        path=Path(directory.name)/'relay.sqlite3'
        store=RelayStore(path,'x'*32)
        app=relay_app(store,'s'*32)
        admin={'X-Mumu-Share-Secret':'s'*32}
        old=os.environ.get('MUMU_PUBLIC_BASE_URL')
        os.environ['MUMU_PUBLIC_BASE_URL']='https://sign.example'
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://relay') as client:
                created=(await client.post('/api/admin/groups',headers=admin,json={
                    'name':'计算机科学 1 组','items':[{'name':'A*','course':'计算机科学'}],'pin':'1234',
                })).json()
                token=created['url'].split('#',1)[1]
                public={'Authorization':'Bearer '+token,'X-Mumu-Pin':'1234'}
                waiting=(await client.get('/api/qr/session',headers=public)).json()
                self.assertTrue(waiting['persistent']); self.assertEqual(waiting['group_name'],'计算机科学 1 组')
                first=await client.post('/api/qr/capture',headers=public,json={
                    'text':'https://mobilelearn.chaoxing.com/widget/sign/e?id=404&enc=firstcode1234','source':'image',
                })
                self.assertEqual(first.status_code,200,first.text); first_run=first.json()['run_id']
                active=await client.post('/api/qr/capture',headers=public,json={
                    'text':'https://mobilelearn.chaoxing.com/widget/sign/e?id=404&enc=secondcode1234','source':'image',
                })
                self.assertEqual(active.status_code,409)
                fetched=await client.get('/api/admin/groups/'+created['id'],headers=admin)
                self.assertIn('firstcode1234',fetched.json()['capture_text'])
                bulk=await client.get(
                    '/api/admin/groups',
                    params={'ids':created['id']+',missing'},
                    headers=admin,
                )
                self.assertEqual(bulk.status_code,200,bulk.text)
                self.assertEqual(bulk.json()['groups'][0]['id'],created['id'])
                self.assertEqual(bulk.json()['groups'][0]['run_id'],first_run)
                self.assertIn('firstcode1234',bulk.json()['groups'][0]['capture_text'])
                await client.post('/api/admin/groups/'+created['id']+'/runs/'+first_run+'/status',headers=admin,json={
                    'status':'failed','items':[{'name':'A*','course':'未匹配','title':'二维码签到','kind':'qr','status':'failed','message':'重新扫描'}],
                    'summary':{'course':'计算机科学 1 组','completed':0,'total':1},
                })
                second=await client.post('/api/qr/capture',headers=public,json={
                    'text':'https://mobilelearn.chaoxing.com/widget/sign/e?id=404&enc=thirdcode1234','source':'image',
                })
                self.assertEqual(second.status_code,200,second.text); second_run=second.json()['run_id']
                self.assertNotEqual(first_run,second_run)
                completed=await client.post('/api/admin/groups/'+created['id']+'/runs/'+second_run+'/status',headers=admin,json={
                    'status':'completed','items':[{'name':'A*','course':'计算机科学','title':'二维码签到','kind':'qr','status':'succeeded','message':'平台已确认签到成功'}],
                    'summary':{'course':'计算机科学 1 组','completed':1,'total':1},
                })
                self.assertEqual(completed.json()['status'],'completed')
                await client.post('/api/admin/groups/'+created['id']+'/revoke',headers=admin)
                self.assertEqual((await client.get('/api/qr/session',headers=public)).status_code,410)
            database=sqlite3.connect(path)
            try: stored=database.execute('SELECT capture_token FROM group_runs ORDER BY created_at DESC LIMIT 1').fetchone()[0]
            finally: database.close()
            self.assertIsNone(stored)
        finally:
            if old is None: os.environ.pop('MUMU_PUBLIC_BASE_URL',None)
            else: os.environ['MUMU_PUBLIC_BASE_URL']=old
            directory.cleanup()

    async def test_automation_settings_are_persisted_per_account(self):
        saved=self.service.store.save_auto_settings(self.accounts[0]['id'],{
            'enabled':True,'kinds':['gesture','code'],'course_filter':'计算机科学',
            'interval_seconds':90,'location':{'latitude':30.5,'longitude':114.3,'address':'Classroom','source':'manual'},
        })
        self.assertTrue(saved['enabled'])
        self.assertEqual(saved['kinds'],['gesture','code'])
        self.assertEqual(saved['course_filter'],'计算机科学')
        self.assertEqual(self.service.store.auto_accounts()[0]['account_id'],self.accounts[0]['id'])
        self.assertFalse(self.service.store.auto_settings(self.accounts[1]['id'])['enabled'])
