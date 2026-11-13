from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.db import Database, utc_now
from app.services.attendance.contracts import AttendanceError, SignInput, classify_receipt, parse_activities, parse_courses, qr_credentials
from app.services.attendance.client import AttendanceClient
from app.services.attendance.service import AttendanceService
from app.services.attendance.routes import ShareGateway, attendance_router, share_app


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
        self.assertEqual(classify_receipt('您已签到')[0],'already_signed')
        self.assertNotEqual(classify_receipt('签到不成功')[0],'succeeded')
        self.assertNotEqual(classify_receipt('not success')[0],'succeeded')
        self.assertEqual(classify_receipt('validate_required')[0],'manual_required')
        self.assertEqual(classify_receipt('二维码已过期')[0],'awaiting_input')
        self.assertEqual(classify_receipt('签到已结束')[0],'expired')
        self.assertEqual(classify_receipt('<html>unexpected</html>')[0],'unknown')

    def test_qr_is_bound_to_an_activity_and_is_never_a_fetchable_arbitrary_url(self):
        self.assertEqual(qr_credentials('https://mobilelearn.chaoxing.com/widget/sign/e?activePrimaryId=303&enc=abcdefgh1234','303'),{'enc':'abcdefgh1234'})
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

    async def test_real_gateway_serves_only_share_routes_and_releases_its_port(self):
        gateway=ShareGateway(self.service,0)
        try:
            await gateway.ensure_started()
            async with httpx.AsyncClient(base_url='http://127.0.0.1:'+str(gateway.port)) as client:
                self.assertEqual((await client.get('/s/')).status_code,200)
                self.assertEqual((await client.get('/assets/jsQR.js')).status_code,200)
                self.assertEqual((await client.get('/api/accounts')).status_code,404)
                self.assertEqual((await client.get('/api/share')).status_code,410)
        finally: await gateway.close()
        self.assertIsNone(gateway.task)

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

    async def test_shares_are_hash_only_expiring_revocable_and_do_not_authorize_other_batches(self):
        batch=self.batch(); share=self.service.store.share(batch['id'],15)
        record=self.service.store.resolve_share(share['token']); self.assertEqual(record['batch_id'],batch['id'])
        with self.db.connect() as db:
            raw=dict(db.execute('SELECT * FROM attendance_shares WHERE id=?',(share['id'],)).fetchone())
        self.assertNotIn(share['token'],str(raw)); self.assertEqual(len(raw['token_hash']),64)
        self.service.store.revoke(share['id'])
        with self.assertRaises(AttendanceError): self.service.store.resolve_share(share['token'])
        expired=self.service.store.share(batch['id'],0)
        with self.assertRaises(AttendanceError): self.service.store.resolve_share(expired['token'])

    async def test_revoked_share_cannot_execute_queued_items(self):
        batch=self.batch(); share=self.service.store.share(batch['id'],15)
        self.service.store.revoke(share['id'])
        self.service.start(batch['id'],SignInput(),share=share['token']); await self.finished(batch['id'])
        self.assertEqual(self.submissions,[])

    async def test_cancellation_revokes_shares_and_preserves_unconfirmed_submit(self):
        entered=asyncio.Event()
        original=self.service.client_factory
        class Blocking(original):
            async def submit(self,prepared): entered.set(); await asyncio.Event().wait()
        self.service.client_factory=Blocking
        batch=self.batch(); share=self.service.store.share(batch['id'],15)
        self.service.start(batch['id'],SignInput()); await asyncio.wait_for(entered.wait(),2)
        self.assertTrue(self.service.active(self.accounts[0]['id']))
        result=await self.service.cancel(batch['id'])
        self.assertIn('unknown',[item['status'] for item in result['items']])
        self.assertFalse(self.service.active(self.accounts[0]['id']))
        with self.assertRaises(AttendanceError): self.service.store.resolve_share(share['token'])

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

    async def test_public_share_service_exposes_only_scoped_status_and_never_cookies_or_admin_routes(self):
        batch=self.batch(); share=self.service.store.share(batch['id'],15); app=share_app(self.service)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://share') as client:
            self.assertEqual((await client.get('/api/share')).status_code,410)
            headers={'Authorization':'Bearer '+share['token']}
            response=await client.get('/api/share',headers=headers)
            self.assertEqual(response.status_code,200)
            data=response.json(); self.assertEqual(data['total'],2)
            for private in ('platform_user_id','storage_state_path','account_id','token_hash','upstream_id','profile_dir'):
                self.assertNotIn(private,response.text)
            self.assertEqual((await client.get('/api/accounts',headers=headers)).status_code,404)
            self.assertEqual((await client.get('/api/attendance/batches',headers=headers)).status_code,404)
            bad=await client.post('/api/share/execute',headers=headers,json={'account_id':'other'})
            self.assertEqual(bad.status_code,422)
            submitted=await client.post('/api/share/execute',headers=headers,json={})
            self.assertEqual(submitted.status_code,200); await self.finished(batch['id'])
            self.service.store.revoke(share['id'])
            self.assertEqual((await client.get('/api/share',headers=headers)).status_code,410)
            page=await client.get('/s/'); self.assertIn('no-referrer',page.headers['referrer-policy'])

    async def test_owner_api_creates_batches_and_scoped_shares(self):
        gateway=SimpleNamespace(origin=lambda value:'https://share.example',ensure_started=AsyncMock())
        app=FastAPI(); app.include_router(attendance_router(self.service,gateway))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owner') as client:
            response=await client.post('/api/attendance/batches',json={'activity_ids':[self.rows[0]['id']]})
            self.assertEqual(response.status_code,200,response.text); identifier=response.json()['id']
            share=await client.post('/api/attendance/batches/'+identifier+'/shares',json={'minutes':15})
            self.assertEqual(share.status_code,200,share.text); self.assertIn('/s/#',share.json()['url'])
            self.assertEqual((await client.post('/api/attendance/batches/'+identifier+'/actions/execute',json={})).status_code,200)
            await self.finished(identifier)
            self.assertEqual((await client.get('/api/attendance/batches/'+identifier)).json()['completed'],1)
            self.assertEqual((await client.post('/api/attendance/batches/'+identifier+'/shares',json={'minutes':0})).status_code,422)

    async def test_gateway_does_not_accept_insecure_public_origins_or_credentials(self):
        gateway=ShareGateway(self.service,0)
        self.assertEqual(gateway.origin('https://sign.example'),'https://sign.example')
        self.assertEqual(gateway.origin('http://192.168.1.2:8878'),'http://192.168.1.2:8878')
        for value in ('http://sign.example','https://user:password@sign.example','https://sign.example/#token','https://sign.example:wrong'):
            with self.assertRaises(AttendanceError): gateway.origin(value)

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
        for flag in ('ifphoto','openCheckFaceFlag','ifNeedVCode','openPreventCheatFlag'):
            client.json=AsyncMock(side_effect=[{'result':1,'data':{**details,flag:1}},{'result':1,'data':{'status':0}}])
            self.assertEqual((await client.prepare(activity(kind='location'),inputs))['status'],'manual_required')
