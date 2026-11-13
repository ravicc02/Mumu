from __future__ import annotations

import asyncio
import logging
import time

from .client import AttendanceClient
from .contracts import AttendanceError, SignInput, parse_qr_payload
from .store import AttendanceStore, ACTIVE, SUCCESS

logger = logging.getLogger('mumu.attendance')


class AttendanceService:
    def __init__(self, database_provider, client_factory=AttendanceClient):
        self.database_provider = database_provider; self.client_factory = client_factory
        self.tasks = {}; self.collecting = set(); self.account_locks = {}; self.item_locks = {}
        self.slots = asyncio.Semaphore(3)
        self.schema_paths = set()
        self.auto_task = None
        self.auto_accounts_running = set()
        self.auto_groups_running = set()
        self.auto_scan_tasks = set()

    @property
    def store(self):
        database = self.database_provider(); store = AttendanceStore(database)
        if str(database.path) not in self.schema_paths:
            store.initialize(); self.schema_paths.add(str(database.path))
        return store

    def initialize(self): self.store.initialize(); self.store.recover()

    def client(self, account, *, fast=False):
        if self.client_factory is AttendanceClient:
            store = self.store
            assets = store.account_assets(account['id'])
            return AttendanceClient(
                {
                    **account,
                    'attendance_photo_path':assets.get('photo_path'),
                    'attendance_face_path':assets.get('face_path'),
                },
                fast=fast,
                codebook_get=store.codebook_get,
                codebook_put=store.codebook_put,
                codebook_delete=store.codebook_delete,
            )
        return self.client_factory(account)

    async def start_background(self):
        if self.auto_task and not self.auto_task.done():
            return
        self.auto_task = asyncio.create_task(self._auto_group_loop())

    async def _auto_group_loop(self):
        await asyncio.sleep(2)
        while True:
            try:
                for group in self.store.auto_groups():
                    if not group['enabled'] or group['id'] in self.auto_groups_running:
                        continue
                    last_scan = float(group.get('last_scan_at') or 0)
                    if time.time() - last_scan < int(group.get('interval_seconds') or 60):
                        continue
                    self.auto_groups_running.add(group['id'])
                    task = asyncio.create_task(self._auto_group(group))
                    self.auto_scan_tasks.add(task)
                    task.add_done_callback(self.auto_scan_tasks.discard)
                    task.add_done_callback(lambda _, identifier=group['id']: self.auto_groups_running.discard(identifier))
            except Exception:
                logger.exception('Attendance auto group scheduler failed')
            await asyncio.sleep(5)

    async def _auto_group(self, group):
        try:
            allowed = {kind for kind in group.get('kinds') or [] if kind in {'normal','photo','gesture','code','location'}}
            location = (group.get('location') or self.store.global_location()) if 'location' in allowed else None
            rows, failures = [], []
            for account_id in group['account_ids']:
                try:
                    await self.collect(account_id)
                    rows.extend(
                        row for row in self.store.activities([account_id])
                        if row['kind'] in allowed
                    )
                except Exception as error:
                    failures.append(f"{account_id}:{str(error)[:120]}")
            rows = list({row['id']:row for row in rows}.values())[:100]
            if rows:
                batch = self.store.create_batch([row['id'] for row in rows])
                self.start(batch['id'],SignInput(location=location) if location else SignInput(),source='auto_group')
                self.store.mark_auto_group_scan(group['id'],batch_id=batch['id'],error='；'.join(failures))
                logger.info('Attendance auto group queued',extra={'diagnostic_code':'attendance.auto_group_queued','diagnostic_data':{
                    'auto_group_id':group['id'],'batch_id':batch['id'],'items':len(rows),'failures':len(failures)}})
            else:
                self.store.mark_auto_group_scan(group['id'],error='；'.join(failures))
        except Exception as error:
            self.store.mark_auto_group_scan(group['id'],error=str(error)[:240])
            logger.warning('Attendance auto group failed',exc_info=True,extra={
                'diagnostic_code':'attendance.auto_group_error','diagnostic_data':{'auto_group_id':group['id']}})

    async def _auto_loop(self):
        await asyncio.sleep(2)
        while True:
            try:
                for settings in self.store.auto_accounts():
                    account_id = settings['account_id']
                    last_scan = float(settings.get('last_scan_at') or 0)
                    if account_id in self.auto_accounts_running:
                        continue
                    if time.time() - last_scan < int(settings.get('interval_seconds') or 60):
                        continue
                    self.auto_accounts_running.add(account_id)
                    task = asyncio.create_task(self._auto_account(settings))
                    self.auto_scan_tasks.add(task)
                    task.add_done_callback(self.auto_scan_tasks.discard)
                    task.add_done_callback(lambda _, identifier=account_id: self.auto_accounts_running.discard(identifier))
            except Exception:
                logger.exception('Attendance auto scheduler failed')
            await asyncio.sleep(5)

    async def _auto_account(self, settings):
        account_id = settings['account_id']
        try:
            await self.collect(account_id, settings.get('course_filter') or '')
            allowed = {kind for kind in settings.get('kinds') or [] if kind not in {'qr','photo'}}
            rows = [row for row in self.store.activities([account_id]) if row['kind'] in allowed]
            if not rows:
                self.store.mark_auto_scan(account_id)
                return
            location = settings.get('location')
            inputs = SignInput(location=location) if location else SignInput()
            batch = self.store.create_batch([row['id'] for row in rows])
            self.start(batch['id'], inputs, source='auto')
            self.store.mark_auto_scan(account_id)
            logger.info('Attendance auto batch queued', extra={'diagnostic_code':'attendance.auto_queued','diagnostic_data':{
                'account_id':account_id,'batch_id':batch['id'],'items':len(rows)}})
        except Exception as error:
            self.store.mark_auto_scan(account_id, str(error)[:240])
            logger.warning('Attendance auto scan failed', exc_info=True, extra={'diagnostic_code':'attendance.auto_error','diagnostic_data':{
                'account_id':account_id}})

    def account(self, identifier):
        account = self.database_provider().get_account(identifier)
        if account['platform'] != 'chaoxing': raise AttendanceError('该平台尚未接入签到')
        if account['status'] != 'ready': raise AttendanceError('请先完成学习通授权')
        return account

    def active(self, account_id):
        return account_id in self.collecting or self.store.account_active(account_id)

    async def collect(self, account_id, course_filter=''):
        account = self.account(account_id)
        if account_id in self.collecting: raise AttendanceError('该账号正在采集签到，请稍候')
        self.collecting.add(account_id)
        try:
            async with self.account_locks.setdefault(account_id,asyncio.Lock()):
                async with self.client(account) as client:
                    courses = await client.courses(); rows, successful, failed, errors = [], [], [], []
                    needle = str(course_filter or '').strip().lower()
                    selected_courses = [course for course in courses if not needle or needle in course['course_name'].lower()]
                    for course in selected_courses:
                        try:
                            rows.extend(await client.activities(course)); successful.append((course['course_id'],course['class_id']))
                        except Exception as error:
                            failed.append((course['course_id'],course['class_id']))
                            errors.append({'course_name':course['course_name'],'message':str(error)[:200] if isinstance(error,AttendanceError) else '课程活动读取失败，请重试；详细原因见 main.log'})
                            logger.warning('Attendance course collection failed',exc_info=True,extra={'diagnostic_code':'attendance.collection_error','diagnostic_data':{'account_id':account_id,'course_id':course['course_id'],'class_id':course['class_id']}})
                    all_courses = None if needle else [(course['course_id'],course['class_id']) for course in courses]
                    activities = self.store.save_activities(account_id,rows,successful,all_courses,failed)
                    logger.info('Attendance collection completed',extra={'diagnostic_code':'attendance.collection','diagnostic_data':{'account_id':account_id,'courses':len(selected_courses),'activities':len(rows),'failed_courses':len(errors)}})
                    return {'account_id':account_id,'activities':activities,'courses':len(selected_courses),'errors':errors,'status':'failed' if selected_courses and len(errors)==len(selected_courses) else 'partial' if errors else 'completed'}
        finally: self.collecting.discard(account_id)

    async def resolve_open_qr_batch(self, batch_id, text, account_ids):
        payload = parse_qr_payload(text)
        active_id = payload.get('active_id') or ''
        if not active_id:
            raise AttendanceError('二维码缺少 activeId，无法自动匹配课程活动')
        account_ids = list(dict.fromkeys(account_ids or []))
        if not account_ids:
            raise AttendanceError('扫码链接没有绑定签到账号')
        batch = self.store.batch(batch_id)
        if batch['items']:
            existing_accounts = {item['account_id'] for item in batch['items']}
            if existing_accounts != set(account_ids) or len(batch['items']) != len(account_ids):
                raise AttendanceError('二维码与当前扫码链接绑定的账号不一致')
            if any(item['kind'] != 'qr' or str(item['upstream_id']) != active_id for item in batch['items']):
                raise AttendanceError('二维码与当前扫码链接已匹配的活动不一致')
            return batch
        found = self.store.resolve_activity_ids(active_id, account_ids)
        missing = [account_id for account_id in account_ids if account_id not in found]
        if missing and self.client_factory is AttendanceClient:
            semaphore = asyncio.Semaphore(3)

            async def match_quickly(account_id):
                async with semaphore:
                    try:
                        async with self.client(self.account(account_id),fast=True) as client:
                            row = await client.quick_activity(active_id)
                        self.store.save_activities(account_id,[row],[],None,[])
                        return True
                    except Exception:
                        return False

            await asyncio.gather(*(match_quickly(account_id) for account_id in missing))
            found = self.store.resolve_activity_ids(active_id, account_ids)
            missing = [account_id for account_id in account_ids if account_id not in found]
        for account_id in missing:
            try:
                await self.collect(account_id)
            except Exception as error:
                logger.warning('Attendance open QR refresh failed', exc_info=True, extra={
                    'diagnostic_code':'attendance.open_qr_collection_error',
                    'diagnostic_data':{'batch_id':batch_id,'account_id':account_id},
                })
        if missing:
            found = self.store.resolve_activity_ids(active_id, account_ids)
            missing = [account_id for account_id in account_ids if account_id not in found]
        if missing:
            names = []
            for account_id in missing:
                try: names.append(self.database_provider().get_account(account_id)['name'])
                except KeyError: names.append('未知账号')
            raise AttendanceError('以下账号没有匹配到该二维码活动：' + '、'.join(names))
        if any(item['kind'] != 'qr' for item in found.values()):
            raise AttendanceError('二维码对应活动不是二维码签到')
        batch = self.store.populate_batch_items(batch_id, [found[account_id]['id'] for account_id in account_ids])
        logger.info('Attendance open QR matched', extra={'diagnostic_code':'attendance.open_qr_matched','diagnostic_data':{
            'batch_id':batch_id,'active_id':active_id,'accounts':len(account_ids)}})
        return batch

    def start(self, identifier, inputs, *, share=None, source='owner'):
        batch = self.store.batch(identifier)
        if identifier in self.tasks and not self.tasks[identifier].done(): return batch
        if any(item['status']=='unknown' for item in batch['items']): raise AttendanceError('有提交结果待核对，请先查询平台记录，不能直接重试')
        candidates = [item for item in batch['items'] if item['status'] in {'pending','awaiting_input','failed','auth_required','cancelled'}]
        if not candidates: return batch
        for item in candidates: self.store.update_item(item['id'],'queued','等待执行')
        logger.info('Attendance batch queued',extra={'diagnostic_code':'attendance.queued','diagnostic_data':{'batch_id':identifier,'items':len(candidates),'source':'share' if share else source}})
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
                async with self.client(account,fast=item['kind']=='qr') as client:
                    if self.client_factory is AttendanceClient:
                        prepared = await client.prepare(item, inputs, fast=item['kind']=='qr')
                    else:
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
                    async with self.client(self.account(item['account_id'])) as client:
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
        if self.auto_task and not self.auto_task.done():
            self.auto_task.cancel()
            await asyncio.gather(self.auto_task, return_exceptions=True)
        self.auto_task = None
        scan_tasks = [task for task in self.auto_scan_tasks if not task.done()]
        for task in scan_tasks:
            task.cancel()
        if scan_tasks:
            await asyncio.gather(*scan_tasks, return_exceptions=True)
        self.auto_scan_tasks.clear()
        tasks = [task for task in self.tasks.values() if not task.done()]
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
