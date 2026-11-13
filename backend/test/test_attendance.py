from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.db import Database, utc_now
from app.services.attendance.contracts import AttendanceError, SignInput, classify_receipt, parse_activities, parse_courses, parse_qr_payload, qr_credentials
from app.services.attendance.client import AttendanceClient
from app.services.attendance.service import AttendanceService
from app.services.attendance.routes import attendance_router
from app.services.attendance.relay import RelayClient, RelayStore, RemoteShareGateway, relay_app


def course(): return {'course_id':'101','class_id':'202','course_name':'Test classroom'}
def activity(number='303', kind='normal'): return {**course(),'upstream_id':number,'title':'Test attendance','kind':kind,
    'start_time':time.time()-60,'end_time':time.time()+3600,'availability_status':'open','verified_at':utc_now(),'open_ended':False}


class AttendanceContractTests(unittest.TestCase):
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
        self.assertEqual(classify_receipt('errorLocation_12.3')[0],'failed')
        self.assertNotEqual(classify_receipt('签到不成功')[0],'succeeded')
        self.assertNotEqual(classify_receipt('not success')[0],'succeeded')
        self.assertEqual(classify_receipt('validate_required')[0],'manual_required')
        self.assertEqual(classify_receipt('二维码已过期')[0],'awaiting_input')
        self.assertEqual(classify_receipt('签到已结束')[0],'expired')
        self.assertEqual(classify_receipt('<html>unexpected</html>')[0],'unknown')

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
        for value in ({},{'result':1,'data':{}},{'result':0,'data':{'activeList':[]}}):
            with self.assertRaises(AttendanceError): parse_activities(value,course())

    def test_encrypted_gesture_code_is_an_accepted_input_shape(self):
        code='AilJpeOmQAA6CCTI81DWdg=='
        self.assertEqual(SignInput(code=code).code,code)

    def test_courses_are_account_scoped_class_pairs_and_malformed_data_does_not_become_empty(self):
        payload={'result':1,'channelList':[{'content':{'id':202,'state':0,'course':{'data':[{'id':101,'name':'Course'}]}}},{'content':{'id':203,'state':1,'course':{'data':[{'id':101}]}}}]}
        self.assertEqual(parse_courses(payload),[{'course_id':'101','class_id':'202','course_name':'Course'}])
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

    async def test_restart_conservatively_marks_inflight_submission_unknown(self):
        batch=self.batch(); self.service.store.update_item(batch['items'][0]['id'],'submitting','Waiting')
        self.service.store.update_item(batch['items'][1]['id'],'queued','Waiting')
        self.service.initialize(); states=[item['status'] for item in self.service.store.batch(batch['id'])['items']]
        self.assertEqual(states,['unknown','cancelled']); self.assertEqual(self.submissions,[])

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
        )
        self.assertEqual(group['kinds'],['normal','photo','code'])
        self.assertEqual(group['participants'][0]['id'],self.accounts[0]['id'])
        updated=self.service.store.save_auto_group(group['id'],enabled=False,name='自动组 2')
        self.assertFalse(updated['enabled']); self.assertEqual(updated['name'],'自动组 2')
        self.service.store.delete_auto_group(group['id'])
        self.assertEqual(self.service.store.auto_groups(),[])

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
            created=await client.post('/api/attendance/auto-groups',json={
                'name':'自动组','account_ids':[self.accounts[0]['id']],'kinds':['normal','code'],
                'interval_seconds':60,'enabled':True,'location':None,
            })
            self.assertEqual(created.status_code,200,created.text)
            group_id=created.json()['id']
            updated=await client.patch('/api/attendance/auto-groups/'+group_id,json={'enabled':False})
            self.assertEqual(updated.status_code,200,updated.text); self.assertFalse(updated.json()['enabled'])
            relay=await client.get('/api/attendance/relay')
            self.assertTrue(relay.json()['configured'])
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
        self.assertEqual(saved['base_url'],'https://sign.example.com')
        self.assertEqual(saved['admin_secret'],'s'*32)
        gateway.configure.assert_awaited_once_with('https://sign.example.com','s'*32)

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

    async def test_client_revalidates_membership_and_never_submits_verification_gated_tasks(self):
        client=AttendanceClient({'name':'Student','platform_user_id':'1000'})
        client.activities=AsyncMock(return_value=[activity()])
        for flags in ({'otherId':0,'ifphoto':1},{'otherId':2,'openCheckFaceFlag':True},{'otherId':5,'showVCode':1},{'otherId':2,'locationText':'Test place','locationRange':50}):
            client.json=AsyncMock(side_effect=[{'result':1,'data':flags},{'result':1,'data':{'status':0}}])
            self.assertEqual((await client.prepare(activity(),SignInput()))['status'],'manual_required')
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
        self.assertEqual(prepared['params']['latitude'],'30.5'); self.assertEqual(prepared['params']['longitude'],'114.3')
        self.assertEqual(prepared['params']['address'],'Actual classroom'); self.assertEqual(prepared['params']['ifTiJiao'],'1')
        client.json=AsyncMock(side_effect=[
            {'result':1,'data':{**details,'openLocationFlag':1}},
            {'result':1,'data':{'status':0}},
        ])
        self.assertEqual((await client.prepare(activity(kind='location'),inputs))['status'],'ready')
        for flag in ('ifphoto','openCheckFaceFlag','ifNeedVCode','openPreventCheatFlag'):
            client.json=AsyncMock(side_effect=[{'result':1,'data':{**details,flag:1}},{'result':1,'data':{'status':0}}])
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
        account_ids=[account['id'] for account in self.accounts]
        waiting={
            'id':'local-group','name':'计算机科学 1 组','revoked':False,'last_status':'waiting',
            'last_remote_run_id':None,'last_batch_id':None,
        }
        running={**waiting,'last_status':'captured','last_remote_run_id':'run-one','last_batch_id':'batch-one'}
        empty={'id':'batch-one','status':'finished','items':[],'completed':0,'total':0,'participants':[]}
        active_item={'account_name':'Alice','course_name':'Course','title':'QR','kind':'qr','status':'queued','message':'waiting'}
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
