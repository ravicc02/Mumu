from __future__ import annotations

import asyncio
import logging

from .client import AttendanceClient
from .contracts import AttendanceError, SignInput
from .store import AttendanceStore, ACTIVE, SUCCESS

logger = logging.getLogger('answer-studio.attendance')


class AttendanceService:
    def __init__(self, database_provider, client_factory=AttendanceClient):
        self.database_provider = database_provider; self.client_factory = client_factory
        self.tasks = {}; self.collecting = set(); self.account_locks = {}; self.item_locks = {}
        self.slots = asyncio.Semaphore(3)
        self.schema_paths = set()

    @property
    def store(self):
        database = self.database_provider(); store = AttendanceStore(database)
        if str(database.path) not in self.schema_paths:
            store.initialize(); self.schema_paths.add(str(database.path))
        return store

    def initialize(self): self.store.initialize(); self.store.recover()

    def account(self, identifier):
        account = self.database_provider().get_account(identifier)
        if account['platform'] != 'chaoxing': raise AttendanceError('该平台尚未接入签到')
        if account['status'] != 'ready': raise AttendanceError('请先完成学习通授权')
        return account

    def active(self, account_id):
        return account_id in self.collecting or self.store.account_active(account_id)

    async def collect(self, account_id):
        account = self.account(account_id)
        if account_id in self.collecting: raise AttendanceError('该账号正在采集签到，请稍候')
        self.collecting.add(account_id)
        try:
            async with self.account_locks.setdefault(account_id,asyncio.Lock()):
                async with self.client_factory(account) as client:
                    courses = await client.courses(); rows, successful, failed, errors = [], [], [], []
                    for course in courses:
                        try:
                            rows.extend(await client.activities(course)); successful.append((course['course_id'],course['class_id']))
                        except Exception as error:
                            failed.append((course['course_id'],course['class_id']))
                            errors.append({'course_name':course['course_name'],'message':str(error)[:200] if isinstance(error,AttendanceError) else '课程活动读取失败，请重试；详细原因见 main.log'})
                            logger.warning('Attendance course collection failed',exc_info=True,extra={'diagnostic_code':'attendance.collection_error','diagnostic_data':{'account_id':account_id,'course_id':course['course_id'],'class_id':course['class_id']}})
                    activities = self.store.save_activities(account_id,rows,successful,[(course['course_id'],course['class_id']) for course in courses],failed)
                    logger.info('Attendance collection completed',extra={'diagnostic_code':'attendance.collection','diagnostic_data':{'account_id':account_id,'courses':len(courses),'activities':len(rows),'failed_courses':len(errors)}})
                    return {'account_id':account_id,'activities':activities,'courses':len(courses),'errors':errors,'status':'failed' if courses and len(errors)==len(courses) else 'partial' if errors else 'completed'}
        finally: self.collecting.discard(account_id)

    def start(self, identifier, inputs, *, share=None):
        batch = self.store.batch(identifier)
        if identifier in self.tasks and not self.tasks[identifier].done(): return batch
        if any(item['status']=='unknown' for item in batch['items']): raise AttendanceError('有提交结果待核对，请先查询平台记录，不能直接重试')
        candidates = [item for item in batch['items'] if item['status'] in {'pending','awaiting_input','failed','auth_required','cancelled'}]
        if not candidates: return batch
        for item in candidates: self.store.update_item(item['id'],'queued','等待执行')
        logger.info('Attendance batch queued',extra={'diagnostic_code':'attendance.queued','diagnostic_data':{'batch_id':identifier,'items':len(candidates),'source':'share' if share else 'owner'}})
        task = asyncio.create_task(self._run(identifier,candidates,inputs,share))
        self.tasks[identifier] = task
        return self.store.batch(identifier)

    async def _run(self, batch_id, items, inputs, share):
        try: await asyncio.gather(*(self._one(batch_id,item,inputs,share) for item in items))
        finally:
            for item in items:
                if self.store.item_status(item['id'])=='queued': self.store.update_item(item['id'],'cancelled','执行已取消，未发出签到请求')

    async def _one(self, batch_id, item, inputs, share):
        try:
            async with self.slots, self.item_locks.setdefault(item['activity_id'],asyncio.Lock()), self.account_locks.setdefault(item['account_id'],asyncio.Lock()):
                if share: self.store.resolve_share(share)
                prior = self.store.prior_outcome(item['activity_id'],item['id'])
                if prior in {'unknown','submitting'}:
                    self.store.update_item(item['id'],'unknown','相同活动已有待确认提交，请先核对原批次'); return
                if prior in SUCCESS:
                    self.store.update_item(item['id'],'already_signed','该账号的同一活动已有成功回执，未重复提交'); return
                self.store.update_item(item['id'],'running','正在核验账号、活动和出勤状态')
                logger.info('Attendance item preflight',extra={'diagnostic_code':'attendance.preflight','diagnostic_data':{'batch_id':batch_id,'item_id':item['id'],'account_id':item['account_id'],'attempt':item['attempt']+1}})
                account = self.account(item['account_id'])
                async with self.client_factory(account) as client:
                    prepared = await client.prepare(item, inputs)
                    if prepared['status'] != 'ready': result = prepared
                    else:
                        if share: self.store.resolve_share(share)
                        self.store.update_item(item['id'],'submitting','正在提交，等待平台回执')
                        logger.info('Attendance submission dispatched',extra={'diagnostic_code':'attendance.submitting','diagnostic_data':{'batch_id':batch_id,'item_id':item['id'],'kind':prepared.get('kind',item['kind']),'automatic_retries':0}})
                        result = await client.submit(prepared)
                    self.store.update_item(item['id'],result['status'],result['message'])
                    logger.info('Attendance item finished',extra={'diagnostic_code':'attendance.result','diagnostic_data':{'batch_id':batch_id,'item_id':item['id'],'account_id':item['account_id'],'activity_id':item['activity_id'],'status':result['status'],'message':result['message']}})
        except asyncio.CancelledError:
            status = self.store.item_status(item['id'])
            if status in ACTIVE:
                self.store.update_item(item['id'],'unknown' if status=='submitting' else 'cancelled','请求可能已送达，请核对平台记录' if status=='submitting' else '已取消，未发出签到请求')
            raise
        except Exception as error:
            current = self.store.item_status(item['id'])
            if current not in ACTIVE:
                logger.warning('Attendance cleanup failed after a recorded result',exc_info=True,extra={'diagnostic_code':'attendance.cleanup_error','diagnostic_data':{'batch_id':batch_id,'item_id':item['id'],'preserved_status':current}})
                return
            status = 'unknown' if current=='submitting' else 'auth_required' if isinstance(error,AttendanceError) and any(word in str(error) for word in ('授权','登录','身份')) else 'failed'
            message = '提交回执未确认，请先查询平台记录' if status=='unknown' else str(error)[:200] if isinstance(error,AttendanceError) else '网络或接口读取失败，请重试；详细原因见 main.log'
            self.store.update_item(item['id'],status,message)
            logger.warning('Attendance operation failed',exc_info=True,extra={'diagnostic_code':'attendance.error','diagnostic_data':{'batch_id':batch_id,'item_id':item['id'],'status':status}})

    async def reconcile(self, identifier):
        batch = self.store.batch(identifier)
        if any(item['status'] in ACTIVE for item in batch['items']): raise AttendanceError('该批次仍在执行，请等待结果或停止后再核对')
        for item in batch['items']:
            if item['status'] not in {'unknown','manual_required','awaiting_input','failed','auth_required'}: continue
            try:
                async with self.account_locks.setdefault(item['account_id'],asyncio.Lock()):
                    async with self.client_factory(self.account(item['account_id'])) as client:
                        result = await client.prepare(item,SignInput())
                        if result['status'] in {'already_signed','expired','manual_required'}: self.store.update_item(item['id'],result['status'],result['message'])
                        elif result['status'] in {'ready','awaiting_input'}: self.store.update_item(item['id'],'awaiting_input','平台显示尚未签到，可重新提供信息后执行')
            except Exception as error:
                message = '请重新授权后再次核对，未重复提交' if isinstance(error,AttendanceError) and any(word in str(error) for word in ('授权','登录','身份')) else '暂时无法核对，请检查网络后重试；详细原因见 main.log'
                self.store.update_item(item['id'],item['status'],message)
                logger.warning('Attendance reconciliation failed',exc_info=True,extra={'diagnostic_code':'attendance.reconcile_error','diagnostic_data':{'batch_id':identifier,'item_id':item['id']}})
        return self.store.batch(identifier)

    async def cancel(self, identifier):
        self.store.revoke_batch(identifier)
        task = self.tasks.get(identifier)
        if task and not task.done(): task.cancel(); await asyncio.gather(task,return_exceptions=True)
        return self.store.batch(identifier)

    async def shutdown(self):
        tasks = [task for task in self.tasks.values() if not task.done()]
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
