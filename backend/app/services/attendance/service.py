from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .class_scan import (
    BURST_SCAN_SECONDS,
    PREWARM_SCAN_SECONDS,
    anchor_sort_key,
    class_scan_summary,
    is_auth_error,
    switch_anchor_for_account,
)
from .client import AttendanceClient
from .contracts import AttendanceError, SignInput, course_in_current_year, current_academic_year, parse_qr_payload
from .pacing import AttendanceRequestPacer
from .store import AttendanceStore, ACTIVE, SUCCESS, normalize_course_filters

logger = logging.getLogger('mumu.attendance')
GROUP_SCAN_JITTER_SECONDS = 5
AUTO_PREWARM_SECONDS = 60
AUTO_BURST_SECONDS = 3 * 60
NORMAL_SIGN_CONCURRENCY = 3
SIGN_DEADLINE_RESERVE_SECONDS = 8
WEEK_SECONDS = 7 * 24 * 3600


def _cooldown_label(seconds):
    seconds = max(1, int(math.ceil(seconds)))
    return f'{math.ceil(seconds / 60)} 分钟' if seconds >= 60 else f'{seconds} 秒'


def _scan_window_open(now=None):
    current = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    return 7 <= current.hour < 22


def _minute_of_day(value):
    hour, minute = (int(part) for part in value.split(':', 1))
    return hour * 60 + minute


def group_scan_window_open(group, now=None):
    windows = group.get('windows') or []
    if not windows:
        return True
    current = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    weekday = current.isoweekday()
    minute = current.hour * 60 + current.minute
    previous = 7 if weekday == 1 else weekday - 1
    for window in windows:
        days = set(window.get('days') or [])
        start = _minute_of_day(window['start'])
        end = _minute_of_day(window['end'])
        if start < end and weekday in days and start <= minute < end:
            return True
        if start > end:
            if weekday in days and minute >= start:
                return True
            if previous in days and minute < end:
                return True
    return False


def auto_group_scan_phase(group, now=None):
    current = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    windows = group.get('windows') or []
    if not windows:
        return {
            'mode': 'normal' if _scan_window_open(current) else 'outside',
            'window': None,
            'seconds_to_start': None,
            'remaining_seconds': None,
        }
    current_week = (
        (current.isoweekday() - 1) * 86400
        + current.hour * 3600
        + current.minute * 60
        + current.second
        + current.microsecond / 1_000_000
    )
    prewarm = None
    active_normal = None
    for window in windows:
        start_minute = _minute_of_day(window['start'])
        end_minute = _minute_of_day(window['end'])
        duration = ((end_minute - start_minute) % (24 * 60)) * 60
        for day in set(window.get('days') or []):
            start = ((int(day) - 1) * 86400 + start_minute * 60) % WEEK_SECONDS
            elapsed = (current_week - start) % WEEK_SECONDS
            if elapsed < duration:
                if elapsed < AUTO_BURST_SECONDS:
                    return {
                        'mode': 'burst',
                        'window': window,
                        'seconds_to_start': 0,
                        'remaining_seconds': max(
                            0,
                            min(duration, AUTO_BURST_SECONDS) - elapsed,
                        ),
                    }
                active_normal = {
                    'mode': 'normal',
                    'window': window,
                    'seconds_to_start': 0,
                    'remaining_seconds': max(0, duration - elapsed),
                }
                continue
            to_start = (start - current_week) % WEEK_SECONDS
            if 0 < to_start <= AUTO_PREWARM_SECONDS:
                if prewarm is None or to_start < prewarm['seconds_to_start']:
                    prewarm = {
                        'mode': 'prewarm',
                        'window': window,
                        'seconds_to_start': to_start,
                        'remaining_seconds': to_start,
                    }
    return prewarm or active_normal or {
        'mode': 'outside',
        'window': None,
        'seconds_to_start': None,
        'remaining_seconds': None,
    }


def auto_group_scan_window_open(group, now=None):
    return auto_group_scan_phase(group, now)['mode'] != 'outside'


def auto_group_jitter_slot(identifier, slots=GROUP_SCAN_JITTER_SECONDS):
    digest = hashlib.sha1(f'attendance-group:{identifier}'.encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big') % max(1, int(slots))


def auto_group_effective_interval(group, mode=None):
    base = max(3, int(group.get('interval_seconds') or 30))
    phase = mode or auto_group_scan_phase(group)['mode']
    if phase == 'burst':
        return min(base, BURST_SCAN_SECONDS)
    if phase == 'prewarm':
        return min(base, PREWARM_SCAN_SECONDS)
    return base


def auto_group_scan_due(group, interval_seconds, *, now=None):
    current = float(now if now is not None else time.time())
    interval = max(3, int(interval_seconds or 60))
    last_scan = float(group.get('last_scan_at') or 0)
    if last_scan > 0:
        return current - last_scan >= interval
    created = float(group.get('created_at') or 0)
    if created <= 0:
        return True
    jitter = auto_group_jitter_slot(
        str(group.get('id') or ''),
        slots=max(1, min(GROUP_SCAN_JITTER_SECONDS, interval)),
    )
    return current >= created + jitter


def adaptive_sign_concurrency(items, *, max_concurrency=8, rush=False, now=None):
    accounts = {
        str(item.get('account_id') or '')
        for item in items or []
        if item.get('account_id')
    }
    if not accounts:
        return 1
    cap = max(1, int(max_concurrency))
    baseline = min(
        cap,
        len(accounts),
        5 if rush else NORMAL_SIGN_CONCURRENCY,
    )
    current = float(now if now is not None else time.time())
    deadlines = []
    for item in items or []:
        try:
            deadline = float(item.get('end_time'))
        except (TypeError, ValueError):
            continue
        if deadline > current:
            deadlines.append(deadline)
    if not deadlines:
        return max(1, baseline)
    remaining = min(deadlines) - current
    available = max(1, remaining - SIGN_DEADLINE_RESERVE_SECONDS)
    needed = math.ceil(len(accounts) / available * 1.15)
    return max(1, min(cap, len(accounts), max(baseline, needed)))


def _env_limit(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return int(default)


def _sign_location(location):
    if not isinstance(location, dict):
        return None
    payload = {
        key:location.get(key)
        for key in ('latitude','longitude','address','source')
        if location.get(key) not in (None,'')
    }
    return payload or None


class AttendanceService:
    def __init__(self, database_provider, client_factory=AttendanceClient):
        self.database_provider = database_provider; self.client_factory = client_factory
        self.tasks = {}; self.collecting = set(); self.account_locks = {}; self.item_locks = {}
        self.sign_concurrency_limit = min(
            20,
            _env_limit('MUMU_ATTENDANCE_SIGN_CONCURRENCY', 8),
        )
        self.request_pacer = AttendanceRequestPacer(
            scan_qps=_env_limit('MUMU_ATTENDANCE_SCAN_QPS', 12),
            sign_qps=_env_limit('MUMU_ATTENDANCE_SIGN_QPS', 10),
        )
        self.slots = asyncio.Semaphore(self.sign_concurrency_limit)
        self.schema_paths = set()
        self.auto_task = None
        self.auto_accounts_running = set()
        self.auto_groups_running = set()
        self.auto_scan_tasks = set()
        self.recovered_batch_ids = []
        self.cancelled_batches = set()
        self.stopping = False
        self.warning_log_times = {}
        self.scan_budget_per_hour = _env_limit('MUMU_ATTENDANCE_SCAN_BUDGET_PER_HOUR', 2400)
        self.sign_budget_per_hour = _env_limit('MUMU_ATTENDANCE_SIGN_BUDGET_PER_HOUR', 1200)
        self.auto_group_slots = asyncio.Semaphore(
            _env_limit('MUMU_ATTENDANCE_AUTO_GROUP_CONCURRENCY', 3)
        )
        self.auto_group_burst_slots = asyncio.Semaphore(
            min(10, _env_limit('MUMU_ATTENDANCE_AUTO_GROUP_BURST_CONCURRENCY', 6))
        )

    @property
    def store(self):
        database = self.database_provider(); store = AttendanceStore(database)
        if str(database.path) not in self.schema_paths:
            store.initialize(); self.schema_paths.add(str(database.path))
        return store

    def initialize(self):
        self.store.initialize()
        self.recovered_batch_ids = self.store.recover()

    def client(self, account, *, fast=False, scan_interval_seconds=60, scan_mode='normal',
               explicit_schedule=False):
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
                scan_mode=scan_mode,
                explicit_schedule=explicit_schedule,
                codebook_get=store.codebook_get,
                codebook_put=store.codebook_put,
                codebook_delete=store.codebook_delete,
                request_guard=lambda kind: self.guard_request(account['id'], kind),
                scan_interval_seconds=scan_interval_seconds,
                class_scan_load=store.class_scan_state,
                class_scan_persist=store.save_class_scan_state,
                code_attempt_get=store.code_attempt,
                code_attempt_note=store.note_code_attempt,
                code_attempt_clear=store.clear_code_attempt,
            )
        return self.client_factory(account)

    def charge_request_budget(self, account_id, kind):
        return self.store.charge_request_budget(
            account_id,
            kind,
            scan_limit=self.scan_budget_per_hour,
            sign_limit=self.sign_budget_per_hour,
        )

    async def guard_request(self, account_id, kind):
        self.charge_request_budget(account_id, kind)
        await self.request_pacer.acquire(kind)

    def scan_budget_exhausted(self, account_id):
        return self.store.request_budget(account_id)['scan_count'] >= self.scan_budget_per_hour

    def sign_budget_exhausted(self, account_id):
        return self.store.request_budget(account_id)['sign_count'] >= self.sign_budget_per_hour

    def warning_log_due(self, key, seconds=300, now=None):
        current = float(now if now is not None else time.monotonic())
        previous = float(self.warning_log_times.get(key) or 0)
        if previous and current - previous < max(1, int(seconds)):
            return False
        self.warning_log_times[key] = current
        if len(self.warning_log_times) > 2000:
            cutoff = current - 3600
            self.warning_log_times = {
                item_key:stamp for item_key,stamp in self.warning_log_times.items()
                if float(stamp or 0) >= cutoff
            }
        return True

    def auto_group(self, identifier, group=None):
        group = group or self.store.auto_group(identifier)
        budgets = {}
        for account_id in group['account_ids']:
            usage = self.store.request_budget(account_id)
            budgets[account_id] = {
                'scan_used':usage['scan_count'],
                'scan_limit':self.scan_budget_per_hour,
                'scan_remaining':max(0,self.scan_budget_per_hour-usage['scan_count']),
                'sign_used':usage['sign_count'],
                'sign_limit':self.sign_budget_per_hour,
                'sign_remaining':max(0,self.sign_budget_per_hour-usage['sign_count']),
            }
        group['account_budgets'] = budgets
        group['class_scan'] = class_scan_summary(group['account_ids'])
        if not group['class_scan']['classes']:
            group['class_scan'] = self.store.class_scan_summary(group['account_ids'])
        phase = auto_group_scan_phase(group)
        group['scan_phase'] = phase['mode']
        group['effective_interval_seconds'] = auto_group_effective_interval(
            group,
            phase['mode'],
        )
        return group

    def auto_groups(self):
        return [self.auto_group(group['id'], group=group) for group in self.store.auto_groups()]

    async def start_background(self):
        if self.auto_task and not self.auto_task.done():
            return
        await self._resume_recovered_batches()
        self.auto_task = asyncio.create_task(self._auto_group_loop())

    async def _resume_recovered_batches(self):
        identifiers = list(dict.fromkeys(self.recovered_batch_ids or []))
        self.recovered_batch_ids = []
        for identifier in identifiers:
            try:
                batch = self.store.batch(identifier)
                if any(item['status'] == 'unknown' for item in batch['items']):
                    logger.warning('Attendance recovered batch requires reconciliation', extra={
                        'diagnostic_code':'attendance.recovery_blocked',
                        'diagnostic_data':{'batch_id':identifier},
                    })
                    continue
                if not any(item['status'] in {'pending','awaiting_input'} for item in batch['items']):
                    continue
                location = _sign_location(self.store.global_location())
                inputs = SignInput(location=location) if location else SignInput()
                self.start(identifier, inputs, source='recovered')
                logger.info('Attendance recovered batch resumed', extra={
                    'diagnostic_code':'attendance.recovered',
                    'diagnostic_data':{'batch_id':identifier},
                })
            except Exception:
                logger.warning('Attendance recovered batch could not resume', exc_info=True, extra={
                    'diagnostic_code':'attendance.recovery_error',
                    'diagnostic_data':{'batch_id':identifier},
                })

    async def _auto_group_loop(self):
        await asyncio.sleep(2)
        while True:
            wake_seconds = GROUP_SCAN_JITTER_SECONDS
            try:
                for group in self.store.auto_groups():
                    if not group['enabled'] or group['id'] in self.auto_groups_running:
                        continue
                    phase = auto_group_scan_phase(group)
                    if phase['mode'] == 'outside':
                        continue
                    if phase['mode'] == 'burst':
                        wake_seconds = min(wake_seconds, 1)
                    elif phase['mode'] == 'prewarm':
                        wake_seconds = min(wake_seconds, 2)
                    interval = auto_group_effective_interval(group, phase['mode'])
                    if not auto_group_scan_due(group, interval):
                        continue
                    self.auto_groups_running.add(group['id'])
                    task = asyncio.create_task(self._auto_group(group, phase=phase))
                    self.auto_scan_tasks.add(task)
                    task.add_done_callback(self.auto_scan_tasks.discard)
                    task.add_done_callback(lambda _, identifier=group['id']: self.auto_groups_running.discard(identifier))
            except Exception:
                logger.exception('Attendance auto group scheduler failed')
            await asyncio.sleep(wake_seconds)

    async def _auto_group(self, group, force=False, phase=None):
        phase = phase or auto_group_scan_phase(group)
        if force:
            phase = {**phase, 'mode':'burst'}
        slots = (
            self.auto_group_burst_slots
            if phase['mode'] in {'prewarm','burst'}
            else self.auto_group_slots
        )
        async with slots:
            await self._auto_group_inner(
                group,
                force=force,
                scan_mode=phase['mode'],
            )

    async def _auto_group_inner(self, group, force=False, scan_mode='normal'):
        try:
            allowed = {kind for kind in group.get('kinds') or [] if kind in {'normal','photo','gesture','code','location'}}
            location = _sign_location(
                (group.get('location') or self.store.global_location())
                if 'location' in allowed else None
            )
            force_scan_token = (
                f"{group['id']}:{time.monotonic_ns()}"
                if force
                else None
            )
            course_filters = normalize_course_filters(
                group.get('course_filters'),
                group.get('course_filter'),
            )
            rows, failures = [], []
            matched_accounts = 0
            def account_label(account_id):
                try: return self.database_provider().get_account(account_id)['name']
                except KeyError: return account_id
            ordered_accounts = sorted(group['account_ids'], key=anchor_sort_key)
            for account_id in ordered_accounts:
                if self.scan_budget_exhausted(account_id):
                    failures.append(
                        f"{account_label(account_id)}：本小时扫描额度已用完（{self.scan_budget_per_hour} 次）"
                    )
                    continue
                throttle = self.store.account_throttle(account_id)
                cooldown = float(throttle.get('cooldown_until') or 0)
                now = time.time()
                if cooldown > now:
                    failures.append(f"{account_label(account_id)}：限流冷却中，约 {_cooldown_label(cooldown - now)}后可检测")
                    continue
                try:
                    result = await self.collect(
                        account_id,
                        course_filters=course_filters,
                        force=force,
                        force_scan_token=force_scan_token,
                        interval_seconds=int(group.get('interval_seconds') or 30),
                        scan_mode=scan_mode,
                        allow_unmatched_courses=bool(course_filters),
                        explicit_schedule=bool(group.get('windows')),
                    )
                    if int(result.get('courses') or 0) > 0:
                        matched_accounts += 1
                    error_messages = [item.get('message','') for item in result.get('errors',[])]
                    throttled = bool(result.get('throttled')) or any(
                        '频繁' in message or '稍后' in message for message in error_messages
                    )
                    if throttled:
                        state = self.store.note_account_throttle(account_id)
                        failures.append(
                            f"{account_label(account_id)}：学习通平台限流，"
                            f"{_cooldown_label(state['delay_seconds'])}后重试"
                        )
                    elif error_messages:
                        failures.extend(f"{account_label(account_id)}：{message}" for message in error_messages[:3])
                    else:
                        self.store.clear_account_throttle(account_id)
                    if not throttled:
                        successful_course_names = {
                            str(name).casefold()
                            for name in result.get('successful_course_names') or []
                            if str(name or '').strip()
                        }
                        if successful_course_names:
                            material_versions = []
                            if allowed & {'normal', 'photo'}:
                                material_versions.append(
                                    self.store.account_assets(account_id).get('updated_at') or ''
                                )
                            if 'location' in allowed:
                                material_versions.append(
                                    (self.store.global_location() or {}).get('updated_at') or ''
                                )
                            rows.extend(
                                row for row in self.store.activities(
                                    [account_id],
                                    auto_only=not force,
                                    retry_after=max(material_versions, default=''),
                                )
                                if row['kind'] in allowed
                                and row['course_name'].casefold() in successful_course_names
                            )
                except Exception as error:
                    error_text = str(error)
                    if is_auth_error(error_text):
                        switched = switch_anchor_for_account(account_id, error_text)
                        if switched:
                            failures.append(
                                f"{account_label(account_id)}：登录状态失效，已切换备用锚点"
                            )
                        else:
                            failures.append(f"{account_label(account_id)}：{error_text[:120]}")
                    elif '频繁' in error_text or '稍后' in error_text:
                        state = self.store.note_account_throttle(account_id)
                        failures.append(
                            f"{account_label(account_id)}：学习通平台限流，"
                            f"{_cooldown_label(state['delay_seconds'])}后重试"
                        )
                    else:
                        failures.append(f"{account_label(account_id)}：{error_text[:120]}")
            if course_filters and matched_accounts == 0:
                failures.append('所选课程当前没有匹配账号：' + '、'.join(course_filters))
            rows = list({row['id']:row for row in rows}.values())[:100]
            if rows:
                batch = self.store.create_batch([row['id'] for row in rows])
                self.start(
                    batch['id'],
                    SignInput(location=location) if location else SignInput(),
                    source='auto_group',
                    rush=force
                    or scan_mode == 'burst'
                    or auto_group_scan_phase(group)['mode'] == 'burst',
                )
                self.store.mark_auto_group_scan(group['id'],batch_id=batch['id'],error='；'.join(failures))
                logger.info('Attendance auto group queued',extra={'diagnostic_code':'attendance.auto_group_queued','diagnostic_data':{
                    'auto_group_id':group['id'],'batch_id':batch['id'],'items':len(rows),'failures':len(failures)}})
            else:
                self.store.mark_auto_group_scan(group['id'],error='；'.join(failures))
        except Exception as error:
            self.store.mark_auto_group_scan(group['id'],error=str(error)[:240])
            logger.warning('Attendance auto group failed',exc_info=True,extra={
                'diagnostic_code':'attendance.auto_group_error','diagnostic_data':{'auto_group_id':group['id']}})

    async def scan_auto_group_now(self, identifier):
        group = self.store.auto_group(identifier)
        if group.get('all_cooling'):
            cooldown_until = float(group.get('cooldown_until') or 0)
            wait = max(1, cooldown_until - time.time())
            raise AttendanceError(f'所有账号仍在限流冷却，请等待 {_cooldown_label(wait)}后再检测')
        if identifier in self.auto_groups_running:
            raise AttendanceError('自动签到分组正在检测，请稍候')
        self.auto_groups_running.add(identifier)
        try:
            if group.get('last_batch_id'):
                previous = self.store.batch(group['last_batch_id'])
                if any(item['status'] == 'unknown' for item in previous['items']):
                    await self.reconcile(group['last_batch_id'])
                    group = self.store.auto_group(identifier)
            await self._auto_group(group, force=True)
        finally:
            self.auto_groups_running.discard(identifier)
        return self.auto_group(identifier)

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
            if self.scan_budget_exhausted(account_id):
                self.store.mark_auto_scan(
                    account_id,
                    f'本小时扫描额度已用完（{self.scan_budget_per_hour} 次）',
                )
                return
            throttle = self.store.account_throttle(account_id)
            cooldown = float(throttle.get('cooldown_until') or 0)
            if cooldown > time.time():
                self.store.mark_auto_scan(
                    account_id,
                    f"学习通平台限流，约 {_cooldown_label(cooldown - time.time())}后可检测",
                )
                return
            result = await self.collect(
                account_id,
                settings.get('course_filter') or '',
                interval_seconds=int(settings.get('interval_seconds') or 60),
            )
            error_messages = [item.get('message','') for item in result.get('errors',[])]
            throttled = bool(result.get('throttled')) or any(
                '频繁' in message or '稍后' in message for message in error_messages
            )
            if throttled:
                state = self.store.note_account_throttle(account_id)
                self.store.mark_auto_scan(
                    account_id,
                    f"学习通平台限流，{_cooldown_label(state['delay_seconds'])}后重试",
                )
                return
            if error_messages:
                self.store.mark_auto_scan(account_id, '；'.join(error_messages[:3]))
                return
            self.store.clear_account_throttle(account_id)
            allowed = {kind for kind in settings.get('kinds') or [] if kind not in {'qr','photo'}}
            rows = [
                row for row in self.store.activities([account_id], auto_only=True)
                if row['kind'] in allowed
            ]
            if not rows:
                self.store.mark_auto_scan(account_id)
                return
            location = _sign_location(settings.get('location'))
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

    async def course_catalog(self, account_ids):
        identifiers = list(dict.fromkeys(str(identifier) for identifier in account_ids if identifier))
        if not identifiers:
            raise AttendanceError('请至少选择一个账号获取课程')
        year = current_academic_year()
        semaphore = asyncio.Semaphore(3)
        errors = []

        async def load(account_id):
            async with semaphore:
                try:
                    account = self.account(account_id)
                    async with self.account_locks.setdefault(account_id, asyncio.Lock()):
                        async with self.client(account, fast=True) as client:
                            courses = (
                                await client.courses(force=True)
                                if isinstance(client, AttendanceClient)
                                else await client.courses()
                            )
                    return account_id, [course for course in courses if course_in_current_year(course, year)], ''
                except Exception as error:
                    return account_id, [], str(error)[:200]

        loaded = await asyncio.gather(*(load(identifier) for identifier in identifiers))
        union = {}
        for account_id, courses, error in loaded:
            if error:
                errors.append({'account_id':account_id,'message':error})
                continue
            for course in courses:
                entry = union.setdefault(course['course_name'], {'account_ids':set(),'class_ids':set()})
                entry['account_ids'].add(account_id)
                entry['class_ids'].add(course['class_id'])
        courses = [{
            'name':name,
            'account_ids':sorted(entry['account_ids']),
            'class_count':len(entry['class_ids']),
            'common':len(entry['account_ids']) == len(identifiers),
        } for name, entry in union.items()]
        courses.sort(key=lambda item:item['name'].casefold())
        return {
            'academic_year':year,
            'semester':f'{year}-{year + 1}',
            'requested_accounts':len(identifiers),
            'courses':courses,
            'errors':errors,
        }

    async def collect(self, account_id, course_filter='', course_filters=None, force=False,
                      force_scan_token=None, interval_seconds=60, scan_mode='normal',
                      allow_unmatched_courses=False, explicit_schedule=False):
        account = self.account(account_id)
        if account_id in self.collecting: raise AttendanceError('该账号正在采集签到，请稍候')
        filters = normalize_course_filters(course_filters, course_filter)
        self.collecting.add(account_id)
        try:
            async with self.account_locks.setdefault(account_id,asyncio.Lock()):
                async with self.client(
                    account,
                    scan_interval_seconds=interval_seconds,
                    scan_mode=scan_mode,
                    explicit_schedule=explicit_schedule,
                ) as client:
                    courses = await client.courses(); rows, successful, failed, errors = [], [], [], []
                    if not courses:
                        raise AttendanceError('未读取到课程列表，请检查学习通授权或稍后重试')
                    year = current_academic_year()
                    current_courses = [
                        course for course in courses
                        if course_in_current_year(course, year)
                    ]
                    if not current_courses:
                        raise AttendanceError(f'{year}-{year + 1} 学年没有可检测课程')
                    throttled = False
                    selected_names = {name.casefold() for name in filters}
                    selected_courses = [
                        course for course in current_courses
                        if not selected_names or course['course_name'].casefold() in selected_names
                    ]
                    if filters and not selected_courses:
                        if allow_unmatched_courses:
                            return {
                                'account_id':account_id,
                                'activities':[],
                                'courses':0,
                                'successful_course_names':[],
                                'errors':[],
                                'throttled':False,
                                'status':'completed',
                            }
                        raise AttendanceError('没有找到所选课程：' + '、'.join(filters))
                    for index, course in enumerate(selected_courses):
                        try:
                            activities = (
                                await client.activities(
                                    course,
                                    force=force,
                                    force_scan_token=force_scan_token,
                                )
                                if isinstance(client, AttendanceClient)
                                else await client.activities(course)
                            )
                            rows.extend(activities); successful.append((course['course_id'],course['class_id']))
                        except Exception as error:
                            failed.append((course['course_id'],course['class_id']))
                            errors.append({'course_name':course['course_name'],'message':str(error)[:200] if isinstance(error,AttendanceError) else '课程活动读取失败，请重试；详细原因见 main.log'})
                            diagnostic = {
                                'account_id':account_id,
                                'course_id':course['course_id'],
                                'class_id':course['class_id'],
                            }
                            if isinstance(error,AttendanceError):
                                diagnostic['error'] = str(error)[:200]
                                if self.warning_log_due((
                                    account_id,
                                    course['course_id'],
                                    course['class_id'],
                                    diagnostic['error'],
                                )):
                                    logger.warning(
                                        'Attendance course collection unavailable',
                                        extra={
                                            'diagnostic_code':'attendance.collection_error',
                                            'diagnostic_data':diagnostic,
                                        },
                                    )
                            else:
                                logger.warning(
                                    'Attendance course collection failed',
                                    exc_info=True,
                                    extra={
                                        'diagnostic_code':'attendance.collection_error',
                                        'diagnostic_data':diagnostic,
                                    },
                                )
                            if isinstance(error, AttendanceError) and ('频繁' in str(error) or '稍后' in str(error)):
                                throttled = True
                                break
                        if len(selected_courses) > 1:
                            await asyncio.sleep(0.1)
                    all_courses = None if filters or throttled else [
                        (course['course_id'],course['class_id'])
                        for course in current_courses
                    ]
                    activities = self.store.save_activities(account_id,rows,successful,all_courses,failed)
                    logger.info('Attendance collection completed',extra={'diagnostic_code':'attendance.collection','diagnostic_data':{'account_id':account_id,'courses':len(selected_courses),'course_filters':filters,'activities':len(rows),'failed_courses':len(errors)}})
                    return {
                        'account_id':account_id,
                        'activities':activities,
                        'courses':len(selected_courses),
                        'successful_course_names':[
                            course['course_name']
                            for course in selected_courses
                            if (course['course_id'],course['class_id']) in successful
                        ],
                        'errors':errors,
                        'throttled':throttled,
                        'status':'failed' if selected_courses and len(errors)==len(selected_courses) else 'partial' if errors else 'completed',
                    }
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
            if not existing_accounts.issubset(set(account_ids)) or len(batch['items']) > len(account_ids):
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
        if not found:
            names = []
            for account_id in missing:
                try: names.append(self.database_provider().get_account(account_id)['name'])
                except KeyError: names.append('未知账号')
            raise AttendanceError('以下账号没有匹配到该二维码活动：' + '、'.join(names))
        if any(item['kind'] != 'qr' for item in found.values()):
            raise AttendanceError('二维码对应活动不是二维码签到')
        matched_ids = [account_id for account_id in account_ids if account_id in found]
        batch = self.store.populate_batch_items(batch_id, [found[account_id]['id'] for account_id in matched_ids])
        logger.info('Attendance open QR matched', extra={'diagnostic_code':'attendance.open_qr_matched','diagnostic_data':{
            'batch_id':batch_id,'active_id':active_id,'accounts':len(matched_ids),'unmatched':len(missing)}})
        return batch

    def start(self, identifier, inputs, *, share=None, source='owner', rush=False):
        batch = self.store.batch(identifier)
        if identifier in self.tasks and not self.tasks[identifier].done(): return batch
        if any(item['status']=='unknown' for item in batch['items']): raise AttendanceError('有提交结果待核对，请先查询平台记录，不能直接重试')
        candidates = [item for item in batch['items'] if item['status'] in {'pending','awaiting_input','failed','auth_required','cancelled'}]
        if not candidates: return batch
        self.cancelled_batches.discard(identifier)
        for item in candidates: self.store.update_item(item['id'],'queued','等待执行')
        logger.info('Attendance batch queued',extra={'diagnostic_code':'attendance.queued','diagnostic_data':{'batch_id':identifier,'items':len(candidates),'source':'share' if share else source}})
        task = asyncio.create_task(self._run(identifier,candidates,inputs,share,rush=rush))
        self.tasks[identifier] = task
        return self.store.batch(identifier)

    async def _run(self, batch_id, items, inputs, share, *, rush=False):
        concurrency = adaptive_sign_concurrency(
            items,
            max_concurrency=self.sign_concurrency_limit,
            rush=rush,
        )
        initial_code_cache = {
            str(item.get('upstream_id') or ''):bool(
                self.store.codebook_get(str(item.get('upstream_id') or ''))
            )
            for item in items
            if item.get('kind') in {'code','gesture'}
            and item.get('upstream_id')
        }
        logger.info('Attendance batch execution planned', extra={
            'diagnostic_code':'attendance.execution_plan',
            'diagnostic_data':{
                'batch_id':batch_id,
                'items':len(items),
                'accounts':len({item['account_id'] for item in items}),
                'concurrency':concurrency,
                'rush':bool(rush),
            },
        })

        async def drain(candidates):
            cursor = 0
            cursor_lock = asyncio.Lock()

            async def worker():
                nonlocal cursor
                while True:
                    async with cursor_lock:
                        if cursor >= len(candidates):
                            return
                        item = candidates[cursor]
                        cursor += 1
                    await self._one(batch_id,item,inputs,share)

            await asyncio.gather(*(worker() for _ in range(concurrency)))

        try:
            await drain(items)
            retry_items = []
            for item in items:
                activity_id = str(item.get('upstream_id') or '')
                if (
                    item.get('kind') in {'code','gesture'}
                    and activity_id
                    and not initial_code_cache.get(activity_id,False)
                    and self.store.codebook_get(activity_id)
                    and self.store.item_status(item['id']) == 'awaiting_input'
                ):
                    retry_items.append(item)
            if retry_items:
                for item in retry_items:
                    self.store.update_item(
                        item['id'],
                        'queued',
                        '同批次账号已取得共享签到码，准备重试',
                    )
                logger.info('Attendance shared code retry', extra={
                    'diagnostic_code':'attendance.shared_code_retry',
                    'diagnostic_data':{
                        'batch_id':batch_id,
                        'items':len(retry_items),
                    },
                })
                await drain(retry_items)
        finally:
            for item in items:
                if self.store.item_status(item['id'])!='queued':
                    continue
                if self.stopping and batch_id not in self.cancelled_batches:
                    self.store.update_item(item['id'],'queued','服务停止，等待下次启动恢复')
                else:
                    self.store.update_item(item['id'],'cancelled','执行已取消，未发出签到请求')

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
                    elif self.sign_budget_exhausted(item['account_id']):
                        result = {
                            'status':'failed',
                            'message':f'该账号本小时签到请求额度已用完（{self.sign_budget_per_hour} 次），请等待下个小时',
                        }
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
                if status=='submitting':
                    self.store.update_item(item['id'],'unknown','请求可能已送达，请核对平台记录')
                elif self.stopping and batch_id not in self.cancelled_batches:
                    self.store.update_item(item['id'],status,'服务停止，等待下次启动恢复')
                else:
                    self.store.update_item(item['id'],'cancelled','已取消，未发出签到请求')
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
        self.cancelled_batches.add(identifier)
        task = self.tasks.get(identifier)
        if task and not task.done(): task.cancel(); await asyncio.gather(task,return_exceptions=True)
        return self.store.batch(identifier)

    async def shutdown(self):
        self.stopping = True
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
