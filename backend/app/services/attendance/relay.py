from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .contracts import AttendanceError, SignInput, is_expired_qr_message


logger = logging.getLogger('mumu.attendance')


def _sha256(value) -> str:
    return hashlib.sha256(str(value or '').encode()).hexdigest()


def _fernet(secret) -> Fernet:
    raw = str(secret or '').encode()
    if not raw:
        raise RuntimeError('扫码中转服务缺少加密密钥')
    try:
        return Fernet(raw)
    except ValueError:
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))


def _qr_payload(text):
    raw = str(text or '').strip()
    if not raw:
        raise ValueError('二维码内容为空')
    active_id = ''
    enc = ''
    if raw.startswith('{'):
        data = json.loads(raw)
        active_id = str(data.get('activeId') or data.get('activePrimaryId') or data.get('id') or '')
        enc = str(data.get('enc') or '')
    else:
        parsed = urlsplit(raw)
        if parsed.scheme == 'https' and parsed.hostname == 'mobilelearn.chaoxing.com' and not parsed.username and not parsed.password:
            values = parse_qs(parsed.query)
            active_id = str((values.get('activeId') or values.get('activePrimaryId') or values.get('id') or [''])[0])
            enc = str((values.get('enc') or [''])[0])
        else:
            enc = raw
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,512}', enc):
        raise ValueError('未识别到有效的签到二维码')
    return {'activeId': active_id, 'enc': enc} if active_id else {'enc': enc}


class RelayStore:
    def __init__(self, path, encryption_key):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cipher = _fernet(encryption_key)
        self.initialize()

    @contextmanager
    def connect(self):
        database = sqlite3.connect(self.path, timeout=15)
        database.row_factory = sqlite3.Row
        try:
            yield database
            database.commit()
        finally:
            database.close()

    def initialize(self):
        with self.connect() as database:
            database.executescript('''
              CREATE TABLE IF NOT EXISTS sessions(
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                pin_hash TEXT, base_url TEXT NOT NULL, expires_at REAL NOT NULL,
                status TEXT NOT NULL, summary_json TEXT NOT NULL, items_json TEXT NOT NULL,
                capture_token TEXT, captured_at REAL, capture_claimed INTEGER NOT NULL DEFAULT 0,
                revoked INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at,revoked);
              CREATE TABLE IF NOT EXISTS groups(
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                pin_hash TEXT, participants_json TEXT NOT NULL, base_url TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS group_runs(
                id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                status TEXT NOT NULL, summary_json TEXT NOT NULL, items_json TEXT NOT NULL,
                capture_token TEXT, captured_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS group_runs_group ON group_runs(group_id,created_at DESC);
            ''')

    def create(self, *, summary, items, base_url, minutes=10, pin='', purpose='qr'):
        identifier = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + minutes * 60
        clean_items = [{
            'name': str(item.get('name') or '')[:1] + '*',
            'course': str(item.get('course') or '')[:200],
            'title': str(item.get('title') or '')[:200],
            'kind': str(item.get('kind') or '')[:32],
            'status': str(item.get('status') or 'pending')[:32],
            'message': str(item.get('message') or '')[:240],
        } for item in items[:100]]
        clean_summary = {
            'course': str(summary.get('course') or '')[:200],
            'title': str(summary.get('title') or '')[:200],
            'purpose': purpose,
            'total': len(clean_items),
            'completed': 0,
        }
        with self.connect() as database:
            database.execute('''INSERT INTO sessions(
                id,token_hash,pin_hash,base_url,expires_at,status,summary_json,items_json,
                capture_token,captured_at,capture_claimed,revoked,created_at,updated_at)
              VALUES(?,?,?,?,?,'waiting',?,?,NULL,NULL,0,0,?,?)''',
              (identifier, _sha256(token), _sha256(pin) if pin else None, base_url.rstrip('/'), expires_at,
               json.dumps(clean_summary, ensure_ascii=False), json.dumps(clean_items, ensure_ascii=False), now, now))
        return {
            'id': identifier, 'token': token, 'url': base_url.rstrip('/') + '/qr/#' + token,
            'expires_at': expires_at, 'requires_pin': bool(pin),
        }

    def _expire(self, record):
        if record and record['expires_at'] <= time.time() and not record['revoked'] and record['status'] not in {'expired', 'revoked'}:
            self.set_status(record['id'], 'expired')
            return True
        return False

    def by_token(self, token):
        if not token or len(token) > 128:
            raise KeyError
        with self.connect() as database:
            record = database.execute('SELECT * FROM sessions WHERE token_hash=?', (_sha256(token),)).fetchone()
        if not record or record['revoked']:
            raise KeyError
        self._expire(record)
        return record

    def by_id(self, identifier):
        with self.connect() as database:
            record = database.execute('SELECT * FROM sessions WHERE id=?', (identifier,)).fetchone()
        if not record:
            raise KeyError
        self._expire(record)
        return record

    def pin_matches(self, record, pin):
        return not record['pin_hash'] or secrets.compare_digest(record['pin_hash'], _sha256(pin))

    def capture(self, record, text, source):
        now = time.time()
        encrypted = self.cipher.encrypt(json.dumps({'text': text, 'source': source}).encode())
        with self.connect() as database:
            changed = database.execute('''UPDATE sessions SET status='captured',capture_token=?,captured_at=?,updated_at=?
              WHERE id=? AND revoked=0 AND expires_at>? AND captured_at IS NULL''',
              (encrypted.decode(), now, now, record['id'], now)).rowcount
        if changed != 1:
            raise AttendanceError('该链接已收到二维码、已过期或已撤销')
        return self.by_id(record['id'])

    def capture_text(self, record):
        if not record['capture_token'] or record['capture_claimed']:
            return ''
        try:
            return json.loads(self.cipher.decrypt(record['capture_token'].encode()).decode()).get('text', '')
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
            return ''

    def acknowledge_capture(self, identifier):
        with self.connect() as database:
            database.execute('''UPDATE sessions SET capture_claimed=1,capture_token=NULL,updated_at=?
              WHERE id=?''', (time.time(), identifier))

    def reopen_capture(self, identifier):
        with self.connect() as database:
            database.execute('''UPDATE sessions SET status='waiting',captured_at=NULL,capture_token=NULL,
              capture_claimed=0,updated_at=? WHERE id=? AND revoked=0''', (time.time(), identifier))
        return self.by_id(identifier)

    def set_status(self, identifier, status, items=None, summary=None):
        with self.connect() as database:
            record = database.execute('SELECT * FROM sessions WHERE id=?', (identifier,)).fetchone()
            if not record:
                raise KeyError
            current_summary = json.loads(record['summary_json'] or '{}')
            current_items = json.loads(record['items_json'] or '[]')
            if summary:
                current_summary.update(summary)
            if items is not None:
                current_items = items[:100]
            database.execute('''UPDATE sessions SET status=?,summary_json=?,items_json=?,updated_at=?
              WHERE id=?''',
              (status, json.dumps(current_summary, ensure_ascii=False),
               json.dumps(current_items, ensure_ascii=False), time.time(), identifier))
        return self.by_id(identifier)

    def revoke(self, identifier):
        with self.connect() as database:
            database.execute('UPDATE sessions SET revoked=1,status=\'revoked\',updated_at=? WHERE id=?',
                             (time.time(), identifier))

    def public(self, record, unlocked=False):
        self._expire(record)
        record = self.by_id(record['id'])
        summary = json.loads(record['summary_json'] or '{}')
        items = json.loads(record['items_json'] or '[]')
        completed = sum(item.get('status') in {'succeeded', 'already_signed'} for item in items)
        return {
            **summary, 'status': record['status'], 'items': items, 'completed': completed,
            'total': len(items), 'expires_at': record['expires_at'],
            'requires_pin': bool(record['pin_hash']), 'unlocked': not record['pin_hash'] or unlocked,
        }

    def create_group(self, *, name, items, base_url, pin=''):
        identifier = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        now = time.time()
        participants = [{
            'name': str(item.get('name') or '')[:1] + '*',
            'course': str(item.get('course') or '')[:200],
        } for item in items[:100]]
        with self.connect() as database:
            database.execute('''INSERT INTO groups(
                id,token_hash,name,pin_hash,participants_json,base_url,revoked,created_at,updated_at)
              VALUES(?,?,?,?,?,?,0,?,?)''',
              (identifier, _sha256(token), str(name or '')[:80], _sha256(pin) if pin else None,
               json.dumps(participants, ensure_ascii=False), base_url.rstrip('/'), now, now))
        return {
            'id': identifier, 'token': token, 'url': base_url.rstrip('/') + '/qr/#' + token,
            'requires_pin': bool(pin), 'persistent': True,
        }

    def lookup_token(self, token):
        if not token or len(token) > 128:
            raise KeyError
        digest = _sha256(token)
        with self.connect() as database:
            session = database.execute('SELECT * FROM sessions WHERE token_hash=?', (digest,)).fetchone()
            if session:
                if session['revoked']:
                    raise KeyError
                self._expire(session)
                return 'session', session
            group = database.execute('SELECT * FROM groups WHERE token_hash=?', (digest,)).fetchone()
        if not group or group['revoked']:
            raise KeyError
        return 'group', group

    def group_by_id(self, identifier):
        with self.connect() as database:
            record = database.execute('SELECT * FROM groups WHERE id=?', (identifier,)).fetchone()
        if not record:
            raise KeyError
        return record

    def group_latest_run(self, group_id):
        with self.connect() as database:
            return database.execute(
                'SELECT * FROM group_runs WHERE group_id=? ORDER BY created_at DESC LIMIT 1',
                (group_id,),
            ).fetchone()

    def group_run(self, group_id, run_id):
        with self.connect() as database:
            return database.execute(
                'SELECT * FROM group_runs WHERE group_id=? AND id=?',
                (group_id, run_id),
            ).fetchone()

    def group_capture_text(self, run):
        if not run or not run['capture_token']:
            return ''
        try:
            return json.loads(self.cipher.decrypt(run['capture_token'].encode()).decode()).get('text', '')
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
            return ''

    def capture_group(self, group, text, source):
        now = time.time()
        encrypted = self.cipher.encrypt(json.dumps({'text': text, 'source': source}).encode())
        participants = json.loads(group['participants_json'] or '[]')
        items = [{
            'name': str(item.get('name') or '同')[:1] + '*',
            'course': '正在接收二维码',
            'title': '二维码签到',
            'kind': 'qr',
            'status': 'captured',
            'message': '已收到二维码，正在匹配课程活动',
        } for item in participants]
        summary = {
            'course': group['name'],
            'title': '二维码签到',
            'purpose': 'qr',
            'total': len(items),
            'completed': 0,
        }
        run_id = uuid.uuid4().hex
        with self.connect() as database:
            database.execute('BEGIN IMMEDIATE')
            current = database.execute('SELECT * FROM groups WHERE id=?', (group['id'],)).fetchone()
            if not current or current['revoked']:
                raise AttendanceError('该分组链接已撤销')
            active = database.execute('''SELECT 1 FROM group_runs
              WHERE group_id=? AND status IN ('captured','queued','signing') LIMIT 1''',
              (group['id'],)).fetchone()
            if active:
                raise AttendanceError('该分组正在处理上一次扫码，请等待本次结果')
            database.execute('''INSERT INTO group_runs(
                id,group_id,status,summary_json,items_json,capture_token,captured_at,created_at,updated_at)
              VALUES(?,?,'captured',?,?,?,?,?,?)''',
              (run_id, group['id'], json.dumps(summary, ensure_ascii=False),
               json.dumps(items, ensure_ascii=False), encrypted.decode(), now, now, now))
        return self.public_group(group, unlocked=True)

    def public_group(self, group, unlocked=False, admin=False):
        group = self.group_by_id(group['id'])
        if group['revoked']:
            return {
                'status': 'revoked', 'items': [], 'completed': 0, 'total': 0,
                'requires_pin': bool(group['pin_hash']), 'unlocked': unlocked,
                'persistent': True, 'group_name': group['name'], 'can_capture': False,
            }
        run = self.group_latest_run(group['id'])
        participants = json.loads(group['participants_json'] or '[]')
        if not admin and run and run['updated_at'] < time.time() - 300 and run['status'] not in {'captured','queued','signing'}:
            run = None
        if run:
            summary = json.loads(run['summary_json'] or '{}')
            items = json.loads(run['items_json'] or '[]')
            active = run['status'] in {'captured','queued','signing'}
            completed = sum(item.get('status') in {'succeeded','already_signed'} for item in items)
            result = {
                **summary, 'status': run['status'], 'items': items, 'completed': completed,
                'total': len(items), 'requires_pin': bool(group['pin_hash']),
                'unlocked': not group['pin_hash'] or unlocked, 'persistent': True,
                'group_name': group['name'], 'run_id': run['id'],
                'captured_at': run['captured_at'], 'can_capture': not active,
            }
            if admin:
                result['capture_text'] = self.group_capture_text(run)
            return result
        items = [{
            'name': str(item.get('name') or '同')[:1] + '*',
            'course': '等待扫码匹配',
            'title': '二维码签到',
            'kind': 'qr',
            'status': 'waiting',
            'message': '扫描教师当前展示的签到二维码',
        } for item in participants]
        return {
            'course': group['name'], 'title': '二维码签到', 'purpose': 'qr',
            'status': 'waiting', 'items': items, 'completed': 0, 'total': len(items),
            'requires_pin': bool(group['pin_hash']), 'unlocked': not group['pin_hash'] or unlocked,
            'persistent': True, 'group_name': group['name'], 'can_capture': True,
            'revoked': False,
        }

    def set_group_run_status(self, group_id, run_id, status, items=None, summary=None):
        now = time.time()
        with self.connect() as database:
            run = database.execute('SELECT * FROM group_runs WHERE group_id=? AND id=?', (group_id, run_id)).fetchone()
            if not run:
                raise KeyError
            current_summary = json.loads(run['summary_json'] or '{}')
            current_items = json.loads(run['items_json'] or '[]')
            if summary:
                current_summary.update(summary)
            if items is not None:
                current_items = items[:100]
            terminal = status in {'completed','partial','failed','expired','revoked'}
            database.execute('''UPDATE group_runs SET status=?,summary_json=?,items_json=?,
              capture_token=?,updated_at=? WHERE group_id=? AND id=?''',
              (status, json.dumps(current_summary, ensure_ascii=False),
               json.dumps(current_items, ensure_ascii=False), None if terminal else run['capture_token'],
               now, group_id, run_id))
        return self.public_group(self.group_by_id(group_id), unlocked=True, admin=True)

    def revoke_group(self, identifier):
        with self.connect() as database:
            database.execute('UPDATE groups SET revoked=1,updated_at=? WHERE id=?', (time.time(), identifier))

    def rename_group(self, identifier, name):
        with self.connect() as database:
            changed = database.execute('UPDATE groups SET name=?,updated_at=? WHERE id=? AND revoked=0',
                                       (str(name or '')[:80],time.time(),identifier)).rowcount
        if changed != 1:
            raise KeyError
        return self.group_by_id(identifier)


class RelayCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: dict = Field(default_factory=dict)
    items: list[dict] = Field(default_factory=list, max_length=100)
    minutes: int = Field(default=10, ge=1, le=15)
    pin: str = Field(default='', max_length=6, pattern=r'^([0-9]{4,6})?$')
    purpose: str = Field(default='qr', pattern=r'^(qr|input)$')


class RelayGroupCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    items: list[dict] = Field(default_factory=list, max_length=100)
    pin: str = Field(default='', max_length=6, pattern=r'^([0-9]{4,6})?$')


class RelayGroupUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)


class RelayStatusRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: str = Field(pattern=r'^(waiting|captured|queued|signing|completed|partial|failed|expired|revoked)$')
    items: list[dict] | None = Field(default=None, max_length=100)
    summary: dict | None = None


class RelayReopenRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[dict] | None = Field(default=None, max_length=100)
    summary: dict | None = None


class RelayUnlockRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RelayCaptureRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=4096)
    source: str = Field(default='image', pattern=r'^(camera|image|paste)$')


def relay_app(store: RelayStore, admin_secret: str):
    if len(str(admin_secret or '')) < 24:
        raise RuntimeError('扫码中转服务管理密钥长度不足')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    recent = {}
    unlocked = {}

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update({
            'Cache-Control': 'no-store',
            'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff',
            'Permissions-Policy': 'camera=(self), microphone=()',
            'Content-Security-Policy': (
                "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            ),
        })
        return response

    def admin_authorized(secret):
        if not secret or not secrets.compare_digest(str(secret), str(admin_secret)):
            raise HTTPException(401, '管理密钥无效')

    def token_authorized(request):
        header = request.headers.get('authorization', '')
        token = header[7:] if header.startswith('Bearer ') else ''
        try:
            return store.lookup_token(token)
        except KeyError as error:
            raise HTTPException(410, '扫码链接无效、已过期或已撤销') from error

    def pin_ok(record, request):
        if not record['pin_hash'] or unlocked.get(record['id'], 0) > time.time():
            return True
        if store.pin_matches(record, request.headers.get('x-mumu-pin', '')):
            expires_at = record['expires_at'] if 'expires_at' in record.keys() else time.time() + 24 * 3600
            unlocked[record['id']] = expires_at
            return True
        raise HTTPException(401, '请输入正确的分享 PIN')

    @app.get('/qr/', response_class=HTMLResponse)
    async def page():
        return Path(__file__).with_name('share.html').read_text(encoding='utf-8')

    @app.get('/assets/qr-decoder.js')
    async def decoder():
        return FileResponse(Path(__file__).with_name('qr-decoder.js'), media_type='application/javascript')

    @app.get('/api/health')
    async def health():
        return {'ok': True, 'service': 'mumu-attendance-share'}

    @app.get('/api/qr/session')
    async def public_session(request: Request):
        kind, record = token_authorized(request)
        is_unlocked = bool(record['pin_hash']) and unlocked.get(record['id'], 0) > time.time()
        if kind == 'group':
            return store.public_group(record, unlocked=is_unlocked)
        return store.public(record, unlocked=is_unlocked)

    @app.post('/api/qr/unlock')
    async def public_unlock(request: Request, _: RelayUnlockRequest):
        _, record = token_authorized(request)
        pin_ok(record, request)
        return {'unlocked': True}

    @app.post('/api/qr/capture')
    async def public_capture(request: Request, payload: RelayCaptureRequest):
        kind, record = token_authorized(request)
        pin_ok(record, request)
        now = time.monotonic()
        history = recent.setdefault(record['id'], [])
        history[:] = [stamp for stamp in history if now - stamp < 60]
        if len(history) >= 8:
            raise HTTPException(429, '提交过于频繁，请稍后再试')
        history.append(now)
        try:
            normalized = json.dumps(_qr_payload(payload.text), ensure_ascii=False)
            if kind == 'group':
                return store.capture_group(record, normalized, payload.source)
            return store.public(store.capture(record, normalized, payload.source), unlocked=True)
        except AttendanceError as error:
            raise HTTPException(409, str(error)) from error
        except (ValueError, json.JSONDecodeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post('/api/admin/groups')
    async def admin_create_group(payload: RelayGroupCreateRequest, x_mumu_share_secret: str = Header(default='')):
        admin_authorized(x_mumu_share_secret)
        base_url = os.environ.get('MUMU_PUBLIC_BASE_URL', '').strip().rstrip('/')
        if not base_url.startswith('https://'):
            raise HTTPException(503, '扫码中转服务未配置 HTTPS 公网地址')
        return store.create_group(
            name=payload.name, items=payload.items, base_url=base_url, pin=payload.pin,
        )

    @app.get('/api/admin/groups/{identifier}')
    async def admin_get_group(identifier: str, x_mumu_share_secret: str = Header(default='')):
        admin_authorized(x_mumu_share_secret)
        try:
            return store.public_group(store.group_by_id(identifier), unlocked=True, admin=True)
        except KeyError as error:
            raise HTTPException(404, '扫码分组不存在') from error

    @app.post('/api/admin/groups/{identifier}/runs/{run_id}/status')
    async def admin_group_status(
        identifier: str,
        run_id: str,
        payload: RelayStatusRequest,
        x_mumu_share_secret: str = Header(default=''),
    ):
        admin_authorized(x_mumu_share_secret)
        try:
            return store.set_group_run_status(
                identifier, run_id, payload.status, payload.items, payload.summary,
            )
        except KeyError as error:
            raise HTTPException(404, '扫码分组或运行记录不存在') from error

    @app.post('/api/admin/groups/{identifier}/revoke')
    async def admin_revoke_group(identifier: str, x_mumu_share_secret: str = Header(default='')):
        admin_authorized(x_mumu_share_secret)
        store.revoke_group(identifier)
        return {'revoked': True}

    @app.patch('/api/admin/groups/{identifier}')
    async def admin_rename_group(
        identifier: str,
        payload: RelayGroupUpdateRequest,
        x_mumu_share_secret: str = Header(default=''),
    ):
        admin_authorized(x_mumu_share_secret)
        try:
            store.rename_group(identifier,payload.name)
        except KeyError as error:
            raise HTTPException(404,'扫码分组不存在或已撤销') from error
        return {'id':identifier,'name':payload.name}

    @app.post('/api/admin/sessions')
    async def admin_create(payload: RelayCreateRequest, x_mumu_share_secret: str = Header(default='')):
        admin_authorized(x_mumu_share_secret)
        base_url = os.environ.get('MUMU_PUBLIC_BASE_URL', '').strip().rstrip('/')
        if not base_url.startswith('https://'):
            raise HTTPException(503, '扫码中转服务未配置 HTTPS 公网地址')
        return store.create(
            summary=payload.summary, items=payload.items, base_url=base_url,
            minutes=payload.minutes, pin=payload.pin, purpose=payload.purpose,
        )

    @app.get('/api/admin/sessions/{identifier}')
    async def admin_get(identifier: str, x_mumu_share_secret: str = Header(default='')):
        admin_authorized(x_mumu_share_secret)
        try:
            record = store.by_id(identifier)
        except KeyError as error:
            raise HTTPException(404, '扫码会话不存在') from error
        return {
            'id': record['id'], 'status': record['status'], 'expires_at': record['expires_at'],
            'capture_text': store.capture_text(record), 'captured_at': record['captured_at'],
            'revoked': bool(record['revoked']),
        }

    @app.post('/api/admin/sessions/{identifier}/ack')
    async def admin_ack(identifier: str, x_mumu_share_secret: str = Header(default='')):
        admin_authorized(x_mumu_share_secret)
        store.acknowledge_capture(identifier)
        return {'acknowledged': True}

    @app.post('/api/admin/sessions/{identifier}/reopen')
    async def admin_reopen(identifier: str, payload: RelayReopenRequest, x_mumu_share_secret: str = Header(default='')):
        admin_authorized(x_mumu_share_secret)
        try:
            record = store.reopen_capture(identifier)
            store.set_status(identifier, 'waiting', payload.items, payload.summary)
        except KeyError as error:
            raise HTTPException(404, '扫码会话不存在') from error
        return {'id': record['id'], 'status': 'waiting'}

    @app.post('/api/admin/sessions/{identifier}/status')
    async def admin_status(identifier: str, payload: RelayStatusRequest, x_mumu_share_secret: str = Header(default='')):
        admin_authorized(x_mumu_share_secret)
        try:
            record = store.set_status(identifier, payload.status, payload.items, payload.summary)
        except KeyError as error:
            raise HTTPException(404, '扫码会话不存在') from error
        return {'id': record['id'], 'status': record['status']}

    @app.post('/api/admin/sessions/{identifier}/revoke')
    async def admin_revoke(identifier: str, x_mumu_share_secret: str = Header(default='')):
        admin_authorized(x_mumu_share_secret)
        store.revoke(identifier)
        return {'revoked': True}
    return app


class RelayClient:
    def __init__(self, base_url, admin_secret):
        self.base_url = base_url.rstrip('/')
        self.headers = {'X-Mumu-Share-Secret': admin_secret}
        self.client = None

    def http(self):
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                timeout=20,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30),
            )
        return self.client

    async def close(self):
        if self.client and not self.client.is_closed:
            await self.client.aclose()
        self.client = None

    async def create(self, payload):
        response = await self.http().post(self.base_url + '/api/admin/sessions', json=payload, headers=self.headers)
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))
        return response.json()

    async def health(self):
        response = await self.http().get(self.base_url + '/api/health', timeout=10)
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))
        payload = response.json()
        if not payload.get('ok'):
            raise AttendanceError('签到服务健康检查未通过')
        return payload

    async def create_group(self, payload):
        response = await self.http().post(self.base_url + '/api/admin/groups', json=payload, headers=self.headers)
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))
        return response.json()

    async def group(self, identifier):
        response = await self.http().get(self.base_url + '/api/admin/groups/' + identifier, headers=self.headers)
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))
        return response.json()

    async def group_status(self, identifier, run_id, payload):
        response = await self.http().post(
            self.base_url + '/api/admin/groups/' + identifier + '/runs/' + run_id + '/status',
            json=payload, headers=self.headers,
        )
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))
        return response.json()

    async def revoke_group(self, identifier):
        response = await self.http().post(
            self.base_url + '/api/admin/groups/' + identifier + '/revoke',
            headers=self.headers,
        )
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))

    async def rename_group(self, identifier, name):
        response = await self.http().patch(
            self.base_url + '/api/admin/groups/' + identifier,
            json={'name':name}, headers=self.headers,
        )
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))

    async def get(self, identifier):
        response = await self.http().get(self.base_url + '/api/admin/sessions/' + identifier, headers=self.headers)
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))
        return response.json()

    async def ack(self, identifier):
        response = await self.http().post(self.base_url + '/api/admin/sessions/' + identifier + '/ack', headers=self.headers)
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))

    async def reopen(self, identifier, items=None, summary=None):
        response = await self.http().post(
            self.base_url + '/api/admin/sessions/' + identifier + '/reopen',
            json={'items': items, 'summary': summary}, headers=self.headers,
        )
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))

    async def status(self, identifier, payload):
        response = await self.http().post(self.base_url + '/api/admin/sessions/' + identifier + '/status', json=payload, headers=self.headers)
        if response.status_code >= 400:
            raise AttendanceError(self._error(response))

    async def revoke(self, identifier):
        await self.http().post(self.base_url + '/api/admin/sessions/' + identifier + '/revoke', headers=self.headers)

    @staticmethod
    def _error(response):
        try:
            detail = response.json().get('detail')
        except (ValueError, AttributeError):
            detail = response.text
        return str(detail or f'扫码服务返回 HTTP {response.status_code}')[:240]


class RemoteShareGateway:
    def __init__(self, service, base_url, admin_secret):
        self.service = service
        self.base_url = ''
        self.admin_secret = ''
        self.client = None
        self.link_cipher = None
        self.tasks = {}
        self.configure_sync(base_url, admin_secret)

    def configure_sync(self, base_url, admin_secret):
        self.base_url = str(base_url or '').strip().rstrip('/')
        self.admin_secret = str(admin_secret or '').strip()
        self.client = RelayClient(self.base_url, self.admin_secret) if self.configured else None
        self.link_cipher = _fernet('mumu-attendance-group:' + (self.admin_secret or 'unconfigured'))

    @property
    def configured(self):
        return bool(self.base_url.startswith('https://') and self.admin_secret)

    def require_configured(self):
        if not self.configured:
            raise AttendanceError('请先在设置中配置签到服务域名和管理密钥')

    async def configure(self, base_url, admin_secret):
        tasks = [task for task in self.tasks.values() if not task.done()]
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
        self.tasks.clear()
        if self.client: await self.client.close()
        self.configure_sync(base_url,admin_secret)

    def settings(self):
        cname_target = os.environ.get('MUMU_ATTENDANCE_RELAY_CNAME_TARGET','').strip()
        return {
            'base_url':self.base_url,
            'admin_secret_configured':bool(self.admin_secret),
            'configured':self.configured,
            'cname_target':cname_target,
        }

    async def ensure_started(self):
        if not self.configured:
            return
        for record in self.service.store.live_qr_groups():
            try: account_ids = json.loads(record.get('account_ids_json') or '[]')
            except (TypeError, ValueError): account_ids = []
            key = ('group',record['remote_group_id'])
            if key in self.tasks and not self.tasks[key].done():
                continue
            task = asyncio.create_task(self._monitor_group(record['id'],record['remote_group_id'],account_ids))
            self.tasks[key] = task
            task.add_done_callback(lambda _, current=key: self.tasks.pop(current, None))

    async def create_group(self, name, account_ids, pin='', base_url=''):
        self.require_configured()
        account_ids = list(dict.fromkeys(account_ids or []))
        if not account_ids:
            raise AttendanceError('扫码链接至少需要一个账号')
        items = []
        database = self.service.database_provider()
        for account_id in account_ids:
            account = database.get_account(account_id)
            items.append({'name':(account['name'] or '同')[:1]+'*','course':name})
        remote = await self.client.create_group({
            'name':str(name or '').strip(), 'items':items, 'pin':pin,
        })
        encrypted = self.link_cipher.encrypt(remote['url'].encode()).decode()
        local = self.service.store.create_qr_group(
            name, account_ids, remote['id'], encrypted, requires_pin=remote['requires_pin'],
        )
        key = ('group',remote['id'])
        task = asyncio.create_task(self._monitor_group(local['id'],remote['id'],account_ids))
        self.tasks[key] = task
        task.add_done_callback(lambda _, current=key: self.tasks.pop(current, None))
        return self._public_group(local, remote['url'])

    def _public_group(self, record, url=''):
        return {
            'id':record['id'],'name':record['name'],'remote_group_id':record['remote_group_id'],
            'account_ids':record['account_ids'],'participants':record['participants'],
            'requires_pin':record['requires_pin'],'created_at':record['created_at'],
            'revoked':record['revoked'],'last_status':record['last_status'],
            'last_scan_at':record['last_scan_at'],'last_batch_id':record['last_batch_id'],
            'url':url or self.link_cipher.decrypt(record['link_ciphertext'].encode()).decode(),
        }

    async def groups(self):
        if not self.configured:
            return []
        return [self._public_group(record) for record in self.service.store.qr_groups()]

    async def revoke_group(self, identifier):
        self.require_configured()
        record = self.service.store.qr_group(identifier)
        await self.client.revoke_group(record['remote_group_id'])
        self.service.store.revoke_qr_group(identifier)
        task = self.tasks.get(('group',record['remote_group_id']))
        if task and not task.done(): task.cancel()

    async def rename_group(self, identifier, name):
        record = self.service.store.qr_group(identifier)
        await self.client.rename_group(record['remote_group_id'],name)
        self.service.store.rename_qr_group(identifier,name)
        return self._public_group(self.service.store.qr_group(identifier))

    def _group_items(self, batch, account_ids):
        return self._public(batch,'open',account_ids)

    async def _monitor_group(self, local_group_id, remote_group_id, account_ids):
        run_status = None
        while True:
            try:
                local = self.service.store.qr_group(local_group_id)
                if local['revoked']:
                    return
                remote = await self.client.group(remote_group_id)
                if remote['status'] == 'revoked':
                    self.service.store.set_qr_group_status(local_group_id,'revoked')
                    return
                run_id = remote.get('run_id')
                if not run_id:
                    if local['last_status'] != 'waiting':
                        self.service.store.set_qr_group_status(local_group_id,'waiting')
                    await asyncio.sleep(0.25)
                    continue
                if remote['status'] in {'completed','partial','failed','expired','revoked'} and local.get('last_remote_run_id') == run_id:
                    if local['last_status'] != remote['status']:
                        self.service.store.set_qr_group_status(local_group_id,remote['status'])
                    await asyncio.sleep(0.5)
                    continue
                if run_id != local.get('last_remote_run_id'):
                    batch = self.service.store.create_open_qr_batch(account_ids)
                    self.service.store.set_qr_group_run(local_group_id,run_id,batch['id'],'captured',scanned=True)
                    local = self.service.store.qr_group(local_group_id)
                batch_id = local.get('last_batch_id')
                if not batch_id:
                    await asyncio.sleep(0.25)
                    continue
                batch = self.service.store.batch(batch_id)
                if not batch['items']:
                    capture_text = remote.get('capture_text') or ''
                    if not capture_text:
                        self.service.store.set_qr_group_status(local_group_id,'failed')
                        await self.client.group_status(remote_group_id,run_id,{
                            'status':'failed','items':[{
                                'name':'同*','course':'二维码读取失败','title':'二维码签到','kind':'qr',
                                'status':'failed','message':'未取得有效二维码，请重新扫描',
                            }],'summary':{'course':local['name'],'completed':0,'total':1},
                        })
                        await asyncio.sleep(0.25)
                        continue
                    try:
                        await self.service.resolve_open_qr_batch(batch_id,capture_text,account_ids)
                    except AttendanceError as error:
                        failed = [{
                            'name':(self.service.database_provider().get_account(account_id)['name'] or '同')[:1]+'*',
                            'course':'未匹配到活动','title':'二维码签到','kind':'qr',
                            'status':'failed','message':str(error)[:240],
                        } for account_id in account_ids]
                        await self.client.group_status(remote_group_id,run_id,{
                            'status':'failed','items':failed,
                            'summary':{'course':local['name'],'completed':0,'total':len(failed)},
                        })
                        self.service.store.set_qr_group_status(local_group_id,'failed')
                        await asyncio.sleep(0.25)
                        continue
                    batch = self.service.store.batch(batch_id)
                payload = self._group_items(batch,account_ids)
                if self._needs_qr_rescan(batch):
                    await self.client.group_status(remote_group_id,run_id,{
                        'status':'failed','items':payload['items'],
                        'summary':{'course':local['name'],'completed':batch['completed'],'total':payload['summary']['total']},
                    })
                    self.service.store.set_qr_group_status(local_group_id,'failed')
                    await asyncio.sleep(0.25)
                    continue
                if batch['status'] in {'completed','needs_review','finished'}:
                    await self.client.group_status(remote_group_id,run_id,{
                        'status':payload['status'],'items':payload['items'],
                        'summary':{'course':local['name'],'completed':batch['completed'],'total':payload['summary']['total']},
                    })
                    self.service.store.set_qr_group_status(local_group_id,payload['status'])
                    await asyncio.sleep(0.5)
                    continue
                if batch['items']:
                    if run_status != f'{run_id}:running':
                        self.service.store.set_qr_group_run(local_group_id,run_id,batch_id,'running')
                        run_status = f'{run_id}:running'
                    self.service.start(batch_id,SignInput(qr=remote.get('capture_text') or ''),source='long_link')
                await asyncio.sleep(0.35)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning('Attendance long link monitor failed',exc_info=True,extra={
                    'diagnostic_code':'attendance.long_link_error',
                    'diagnostic_data':{'group_id':local_group_id,'remote_group_id':remote_group_id},
                })
                await asyncio.sleep(1)

    def origin(self, configured=''):
        value = (configured or self.base_url).rstrip('/')
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {'', '/'}:
            raise AttendanceError('远程扫码服务必须使用 HTTPS 域名')
        return value

    def _public(self, batch, mode='activity', account_ids=None):
        items = [{
            'name': item['account_name'][:1] + '*', 'course': item['course_name'],
            'title': item['title'], 'kind': item['kind'], 'status': item['status'],
            'message': item['message'],
        } for item in batch['items']]
        if mode == 'open' and not items:
            for account_id in dict.fromkeys(account_ids or []):
                try: name = self.service.database_provider().get_account(account_id)['name']
                except KeyError: name = '同'
                items.append({
                    'name':name[:1]+'*', 'course':'等待扫码匹配', 'title':'二维码签到',
                    'kind':'qr', 'status':'waiting', 'message':'等待同学扫描教师二维码',
                })
        return {
            'items': items,
            'status': 'waiting' if mode == 'open' and not batch['items'] else 'signing' if batch['status'] == 'running' else (
                'completed' if batch['status'] == 'completed' else
                'partial' if batch['status'] == 'needs_review' else
                'queued' if batch['status'] == 'awaiting_input' else 'failed'
            ),
            'summary': {'completed':batch['completed'],'total':len(items) if mode == 'open' and not batch['items'] else batch['total']},
        }

    @staticmethod
    def _needs_qr_rescan(batch):
        return batch['status'] not in {'queued','running'} and any(
            item['kind'] == 'qr'
            and item['status'] == 'awaiting_input'
            and is_expired_qr_message(item['message'])
            for item in batch['items']
        )

    async def _monitor(self, session_id, batch_id, mode='activity', account_ids=None):
        captured = False
        capture_detected_at = None
        try:
            while True:
                session = await self.client.get(session_id)
                if session['revoked'] or session['status'] in {'expired', 'revoked'}:
                    return
                if session.get('capture_text') and not captured:
                    captured = True
                    capture_detected_at = time.monotonic()
                    try:
                        if mode == 'open':
                            await self.service.resolve_open_qr_batch(batch_id, session['capture_text'], account_ids or [])
                        self.service.start(batch_id, SignInput(qr=session['capture_text']), source='remote_share')
                    except AttendanceError as error:
                        failed_items = [{
                            'name':(self.service.database_provider().get_account(account_id)['name'] or '同')[:1]+'*',
                            'course':'未匹配到活动','title':'二维码签到','kind':'qr',
                            'status':'failed','message':str(error)[:240],
                        } for account_id in account_ids or []]
                        await self.client.reopen(session_id, failed_items, {
                            'completed':0,'total':len(failed_items),'course':'请重新扫描',
                        })
                        captured = False
                        await asyncio.sleep(0.2)
                        continue
                    except Exception:
                        failed_items = [{
                            'name':'同*','course':'匹配失败','title':'二维码签到','kind':'qr',
                            'status':'failed','message':'自动匹配课程活动失败，请重新扫描',
                        }]
                        await self.client.reopen(session_id, failed_items, {
                            'completed':0,'total':1,'course':'请重新扫描',
                        })
                        captured = False
                        await asyncio.sleep(0.2)
                        continue
                batch = self.service.store.batch(batch_id)
                payload = self._public(batch, mode, account_ids)
                if self._needs_qr_rescan(batch):
                    await self.client.reopen(session_id, payload['items'], {
                        'completed':batch['completed'],'total':payload['summary']['total'],
                        'course':batch['items'][0]['course_name'] if batch['items'] else '请重新扫描',
                    })
                    captured = False
                    await asyncio.sleep(0.2)
                    continue
                if batch['status'] in {'completed', 'needs_review', 'finished'}:
                    status_payload = {
                        'status': payload['status'], 'items': payload['items'],
                        'summary': {
                            'completed':batch['completed'],
                            'total':payload['summary']['total'],
                            'course':batch['items'][0]['course_name'] if batch['items'] else '等待扫码匹配',
                        },
                    }
                    results = await asyncio.gather(
                        self.client.ack(session_id),
                        self.client.status(session_id, status_payload),
                        return_exceptions=True,
                    )
                    failures = [result for result in results if isinstance(result, Exception)]
                    if failures:
                        logger.warning('Attendance remote result delivery failed', extra={
                            'diagnostic_code':'attendance.remote_result_error',
                            'diagnostic_data':{'batch_id':batch_id,'remote_session_id':session_id,'failures':len(failures)},
                        })
                    if capture_detected_at is not None:
                        logger.info('Attendance remote share finalized', extra={
                            'diagnostic_code':'attendance.remote_timing',
                            'diagnostic_data':{
                                'batch_id':batch_id,
                                'remote_session_id':session_id,
                                'result':batch['status'],
                                'capture_to_result_ms':round((time.monotonic()-capture_detected_at)*1000),
                            },
                        })
                    return
                if session['expires_at'] <= time.time():
                    return
                await asyncio.sleep(0.35 if captured else 0.25)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def create(self, batch_id, minutes, pin='', purpose='qr', mode='activity', account_ids=None, base_url=''):
        batch = self.service.store.batch(batch_id)
        if purpose == 'qr' and mode == 'activity':
            kinds = {item['kind'] for item in batch['items']}
            activities = {item['upstream_id'] for item in batch['items']}
            if kinds != {'qr'} or len(activities) != 1:
                raise AttendanceError('扫码链接只能绑定一个二维码签到活动')
        account_ids = list(dict.fromkeys(account_ids or []))
        public = self._public(batch, mode, account_ids)
        session = await self.client.create({
            'summary': {
                'course':batch['items'][0]['course_name'] if batch['items'] else '等待扫码匹配',
                'title':batch['items'][0]['title'] if batch['items'] else '二维码签到',
                'purpose':purpose,'mode':mode,
            },
            'items': public['items'],
            'minutes': minutes, 'pin': pin, 'purpose': purpose,
        })
        local_share = self.service.store.share(
            batch_id, minutes, purpose=purpose, pin=pin,
            remote_session_id=session['id'], mode=mode, account_ids=account_ids,
        )
        task = asyncio.create_task(self._monitor(session['id'], batch_id, mode, account_ids))
        self.tasks[(session['id'], batch_id)] = task
        task.add_done_callback(lambda _: self.tasks.pop((session['id'], batch_id), None))
        return {**session, 'id':local_share['id'], 'remote_session_id':session['id'], 'mode':mode}

    async def revoke(self, identifier):
        record = self.service.store.share_record(identifier)
        if record.get('remote_session_id'):
            await self.client.revoke(record['remote_session_id'])
        self.service.store.revoke(identifier)

    async def get(self, identifier):
        record = self.service.store.share_record(identifier)
        if record.get('remote_session_id'):
            remote = await self.client.get(record['remote_session_id'])
            return {**record, **remote, 'id':identifier}
        return record

    async def close(self):
        tasks = [task for task in self.tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.client:
            await self.client.close()
