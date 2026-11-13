from __future__ import annotations

import asyncio
from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import socket
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .contracts import AttendanceError, SignInput, is_expired_qr_message, parse_qr_payload, qr_credentials


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    activity_ids: list[str] = Field(min_length=1,max_length=100)


class LongQrGroupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=80)
    account_ids: list[str] = Field(min_length=1,max_length=100)
    base_url: str = Field(default='',max_length=2048)
    pin: str = Field(default='',max_length=6,pattern=r'^([0-9]{4,6})?$')


class QrGroupRenameRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=80)


class LocationMaterial(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)
    latitude: float = Field(ge=-90,le=90)
    longitude: float = Field(ge=-180,le=180)
    address: str = Field(min_length=1,max_length=200)


class AutoGroupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=80)
    account_ids: list[str] = Field(min_length=1,max_length=100)
    kinds: list[str] = Field(min_length=1,max_length=6)
    interval_seconds: int = Field(default=60,ge=30,le=3600)
    enabled: bool = True
    location: LocationMaterial | None = None


class AutoGroupUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None,min_length=1,max_length=80)
    account_ids: list[str] | None = Field(default=None,min_length=1,max_length=100)
    kinds: list[str] | None = Field(default=None,min_length=1,max_length=6)
    interval_seconds: int | None = Field(default=None,ge=30,le=3600)
    enabled: bool | None = None
    location: LocationMaterial | None = None


class RelaySettingsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = Field(min_length=1,max_length=2048)
    admin_secret: str = Field(default='',max_length=512)


class QrCaptureRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1,max_length=4096)
    source: str = Field(default='image',pattern=r'^(camera|image|paste)$')


def public_batch(batch):
    return {'id':batch['id'],'status':batch['status'],'completed':batch['completed'],'total':batch['total'],
            'items':[{'id':item['id'],'name':item['account_name'][:1]+'*','course':item['course_name'],
                      'title':item['title'],'kind':item['kind'],'status':item['status'],'message':item['message']} for item in batch['items']]}


def public_share_batch(service, record):
    batch = service.store.batch(record['batch_id'])
    if record.get('mode') != 'open' or batch['items']:
        return public_batch(batch)
    account_ids = []
    try: account_ids = json.loads(record.get('account_ids_json') or '[]')
    except (TypeError, ValueError): pass
    participants = {account['id']: account for account in batch.get('participants', [])}
    items = [{
        'id':'participant-'+account_id,
        'name':(participants.get(account_id,{}).get('name') or '同')[:1]+'*',
        'course':'等待扫码匹配',
        'title':'二维码签到',
        'kind':'qr',
        'status':'waiting',
        'message':'等待同学扫描教师二维码',
    } for account_id in account_ids]
    return {'id':batch['id'],'status':'waiting','completed':0,'total':len(items),'items':items}


def share_app(service):
    app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    recent = {}
    unlocked = {}

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    def authorized(request):
        header = request.headers.get('authorization','')
        token = header[7:] if header.startswith('Bearer ') else ''
        try: record = service.store.resolve_share(token)
        except AttendanceError as error: raise HTTPException(410,str(error)) from error
        return record,token

    def require_pin(record, request):
        if not record.get('pin_hash'):
            return
        stamp = unlocked.get(record['id'],0)
        if stamp > time.time():
            return
        pin = request.headers.get('x-mumu-pin','')
        if not service.store.pin_matches(record,pin):
            raise HTTPException(401,'请输入正确的分享 PIN')
        unlocked[record['id']] = min(record['expires_at'],time.time()+record['expires_at']-time.time())

    @app.get('/s/', response_class=HTMLResponse)
    async def page(): return Path(__file__).with_name('share.html').read_text(encoding='utf-8')

    @app.get('/assets/qr-decoder.js')
    async def decoder(): return FileResponse(Path(__file__).with_name('qr-decoder.js'),media_type='application/javascript')

    @app.get('/api/share')
    async def status(request: Request):
        record,_ = authorized(request)
        return {**public_share_batch(service,record),'expires_at':record['expires_at'],
                'requires_pin':bool(record.get('pin_hash')),'unlocked':not record.get('pin_hash') or unlocked.get(record['id'],0)>time.time(),
                'purpose':record.get('purpose') or 'input','mode':record.get('mode') or 'activity'}

    @app.post('/api/share/unlock')
    async def unlock(request: Request):
        record,_ = authorized(request)
        require_pin(record,request)
        return {'unlocked':True}

    @app.post('/api/share/capture')
    async def capture(request: Request, payload: QrCaptureRequest):
        record,token = authorized(request)
        require_pin(record,request)
        existing_batch = service.store.batch(record['batch_id'])
        if record.get('captured_at') and existing_batch['status'] not in {'queued','running','submitting'} and any(
            item['kind']=='qr' and item['status']=='awaiting_input'
            and is_expired_qr_message(item['message'])
            for item in existing_batch['items']
        ):
            service.store.reopen_share(record['id'])
        try:
            if record.get('mode') == 'open':
                try: account_ids = json.loads(record.get('account_ids_json') or '[]')
                except (TypeError, ValueError): account_ids = []
                parsed = parse_qr_payload(payload.text)
                if not parsed['active_id']:
                    raise AttendanceError('二维码缺少 activeId，无法自动匹配课程活动')
                await service.resolve_open_qr_batch(record['batch_id'],payload.text,account_ids)
                service.store.capture_share(record['id'],payload.text)
            else:
                batch = service.store.batch(record['batch_id'])
                activities = {item['upstream_id'] for item in batch['items']}
                if len(activities) != 1 or any(item['kind'] != 'qr' for item in batch['items']):
                    raise AttendanceError('该链接不是二维码签到会话')
                qr_credentials(payload.text,next(iter(activities)))
                service.store.capture_share(record['id'],payload.text)
            service.start(record['batch_id'],SignInput(qr=payload.text),share=token,source='share')
            return public_share_batch(service,record)
        except AttendanceError as error:
            raise HTTPException(409,str(error)) from error

    @app.post('/api/share/execute')
    async def submit(request: Request, payload: SignInput):
        record,token = authorized(request)
        now = time.monotonic()
        history = recent.setdefault(record['id'],[])
        history[:] = [stamp for stamp in history if now-stamp < 60]
        if len(history)>=6: raise HTTPException(429,'提交过于频繁，请稍后重试')
        if len(recent)>1000:
            for key in [key for key,values in recent.items() if not values or now-values[-1]>60]: recent.pop(key,None)
        history.append(now)
        try: return public_batch(service.start(record['batch_id'],payload,share=token))
        except AttendanceError as error: raise HTTPException(409,str(error)) from error
    return app


class ShareGateway:
    def __init__(self, service, port=8878):
        self.service = service
        self.app = share_app(service); self.port = port
        self.server = self.task = self.socket = None
        self.lock = asyncio.Lock()

    async def ensure_started(self):
        async with self.lock:
            if self.task and not self.task.done(): return
            import uvicorn
            class GatewayServer(uvicorn.Server):
                @contextmanager
                def capture_signals(self): yield
            sock = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            try: sock.bind(('0.0.0.0',self.port)); sock.listen(128); sock.setblocking(False)
            except OSError as error:
                sock.close(); raise AttendanceError(f'分享端口 {self.port} 不可用，请检查端口占用') from error
            self.socket = sock
            self.port = sock.getsockname()[1]
            self.server = GatewayServer(uvicorn.Config(self.app,log_config=None,access_log=False,lifespan='off',server_header=False))
            self.task = asyncio.create_task(self.server.serve(sockets=[sock]))
            for _ in range(50):
                if self.server.started: return
                if self.task.done(): break
                await asyncio.sleep(.02)
            await self.close(); raise AttendanceError('分享服务未能启动')

    async def create(self, batch_id, minutes, *, pin='', purpose='qr', mode='activity', account_ids=None, base_url=''):
        await self.ensure_started()
        created = self.service.store.share(
            batch_id, minutes, purpose=purpose, pin=pin, mode=mode, account_ids=account_ids,
        )
        return {
            'id':created['id'], 'batch_id':batch_id, 'token':created['token'],
            'url':self.origin(base_url)+'/s/#'+created['token'], 'expires_at':created['expires_at'],
            'requires_pin':created['requires_pin'], 'mode':mode,
        }

    def origin(self, configured=''):
        if configured:
            try:
                url = urlsplit(configured)
                if url.port is not None and not 1<=url.port<=65535: raise ValueError()
            except ValueError as error: raise AttendanceError('分享地址格式无效') from error
            try: local = ipaddress.ip_address(url.hostname).is_private
            except ValueError: local = url.hostname in {'localhost','127.0.0.1'}
            if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {'','/'} or (url.scheme=='http' and not local):
                raise AttendanceError('分享地址须为 HTTPS 地址，或本机、局域网 HTTP 地址')
            return configured.rstrip('/')
        try: addresses = [item[4][0] for item in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET) if not item[4][0].startswith('127.')]
        except OSError: addresses = []
        preferred = next((address for address in addresses if address.startswith(('192.168.','10.'))), addresses[0] if addresses else '127.0.0.1')
        return f'http://{preferred}:{self.port}'

    async def close(self):
        if self.server: self.server.should_exit = True
        if self.task:
            try: await asyncio.wait_for(self.task,5)
            except asyncio.TimeoutError: self.task.cancel(); await asyncio.gather(self.task,return_exceptions=True)
        if self.socket: self.socket.close()
        self.task = self.server = self.socket = None


def attendance_router(service, gateway, open_activity=None):
    api = APIRouter(prefix='/api/attendance',tags=['attendance'])

    @api.get('/batches')
    async def batches(): return service.store.batches()

    @api.post('/batches')
    async def create(payload: BatchRequest):
        try: return service.store.create_batch(payload.activity_ids)
        except AttendanceError as error: raise HTTPException(409,str(error)) from error

    @api.get('/batches/{identifier}')
    async def batch(identifier: str):
        try: return service.store.batch(identifier)
        except KeyError as error: raise HTTPException(404,str(error)) from error

    @api.post('/batches/{identifier}/actions/{action}')
    async def control(identifier: str, action: str, payload: SignInput):
        try:
            if action=='execute': return service.start(identifier,payload)
            if action=='reconcile': return await service.reconcile(identifier)
            if action=='cancel': return await service.cancel(identifier)
            raise HTTPException(404,'未知签到操作')
        except KeyError as error: raise HTTPException(404,str(error)) from error
        except AttendanceError as error: raise HTTPException(409,str(error)) from error

    @api.get('/qr-groups')
    async def qr_groups():
        if not hasattr(gateway,'groups'):
            raise HTTPException(409,'当前环境未配置扫码服务')
        return await gateway.groups()

    @api.post('/qr-groups')
    async def create_qr_group(payload: LongQrGroupRequest):
        if not hasattr(gateway,'create_group'):
            raise HTTPException(409,'当前环境未配置扫码服务')
        try:
            return await gateway.create_group(
                payload.name,payload.account_ids,pin=payload.pin,base_url=payload.base_url,
            )
        except KeyError as error: raise HTTPException(404,str(error)) from error
        except AttendanceError as error: raise HTTPException(409,str(error)) from error

    @api.delete('/qr-groups/{identifier}')
    async def revoke_qr_group(identifier: str):
        if not hasattr(gateway,'revoke_group'):
            raise HTTPException(409,'当前环境未配置扫码服务')
        try:
            await gateway.revoke_group(identifier)
            return {'revoked':True}
        except KeyError as error: raise HTTPException(404,str(error)) from error
        except AttendanceError as error: raise HTTPException(409,str(error)) from error

    @api.patch('/qr-groups/{identifier}')
    async def rename_qr_group(identifier: str, payload: QrGroupRenameRequest):
        if not hasattr(gateway,'rename_group'):
            raise HTTPException(409,'当前环境未配置扫码服务')
        try:
            return await gateway.rename_group(identifier,payload.name)
        except KeyError as error: raise HTTPException(404,str(error)) from error
        except AttendanceError as error: raise HTTPException(409,str(error)) from error

    @api.get('/accounts/{identifier}/profile')
    async def get_account_profile(identifier: str):
        try: account = service.database_provider().get_account(identifier)
        except KeyError as error: raise HTTPException(404,'账号不存在') from error
        if account['platform'] != 'chaoxing':
            raise HTTPException(409,'该账号不是学习通账号')
        assets = service.store.account_assets(identifier)
        return {
            'account_id':identifier,
            'photo':bool(assets.get('photo_path')),
            'face':bool(assets.get('face_path')),
            'photo_url':'/api/attendance/accounts/'+identifier+'/assets/photo' if assets.get('photo_path') else '',
            'face_url':'/api/attendance/accounts/'+identifier+'/assets/face' if assets.get('face_path') else '',
            'updated_at':assets.get('updated_at'),
        }

    @api.post('/accounts/{identifier}/assets/{kind}')
    async def upload_account_asset(identifier: str, kind: str, file: UploadFile = File(...)):
        if kind not in {'photo','face'}:
            raise HTTPException(422,'未知的账号素材类型')
        try: account = service.database_provider().get_account(identifier)
        except KeyError as error: raise HTTPException(404,'账号不存在') from error
        if account['platform'] != 'chaoxing':
            raise HTTPException(409,'该账号不是学习通账号')
        data = await file.read(8 * 1024 * 1024 + 1)
        if not data or len(data) > 8 * 1024 * 1024:
            raise HTTPException(422,'素材图片不得超过 8 MB')
        mime = 'image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else (
            'image/jpeg' if data.startswith(b'\xff\xd8\xff') else
            'image/webp' if data.startswith(b'RIFF') and data[8:12] == b'WEBP' else ''
        )
        if not mime:
            raise HTTPException(422,'素材仅支持 PNG、JPEG 或 WebP')
        folder = Path(service.database_provider().settings.data_root) / 'attendance-assets' / identifier
        folder.mkdir(parents=True,exist_ok=True)
        suffix = {'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}[mime]
        target = folder / (kind + suffix)
        temporary = folder / (kind + '.tmp')
        temporary.write_bytes(data)
        temporary.replace(target)
        service.store.save_account_asset(identifier,kind,target,mime)
        return await get_account_profile(identifier)

    @api.get('/accounts/{identifier}/assets/{kind}')
    async def get_account_asset(identifier: str, kind: str):
        if kind not in {'photo','face'}:
            raise HTTPException(404,'素材不存在')
        assets = service.store.account_assets(identifier)
        path = assets.get(kind + '_path')
        mime = assets.get(kind + '_mime')
        if not path or not mime or not Path(path).is_file():
            raise HTTPException(404,'素材尚未配置')
        return FileResponse(path,media_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @api.delete('/accounts/{identifier}/assets/{kind}')
    async def delete_account_asset(identifier: str, kind: str):
        if kind not in {'photo','face'}:
            raise HTTPException(422,'未知的账号素材类型')
        assets = service.store.account_assets(identifier)
        path = assets.get(kind + '_path')
        service.store.delete_account_asset(identifier,kind)
        if path:
            try: Path(path).unlink(missing_ok=True)
            except OSError: pass
        return await get_account_profile(identifier)

    @api.get('/location')
    async def get_global_location():
        return service.store.global_location() or {}

    @api.put('/location')
    async def save_global_location(payload: LocationMaterial):
        return service.store.save_global_location({**payload.model_dump(),'source':'manual'})

    @api.delete('/location')
    async def delete_global_location():
        with service.database_provider().connect() as db:
            db.execute('DELETE FROM attendance_config WHERE id=1')
        return {}

    @api.get('/auto-groups')
    async def auto_groups():
        return service.store.auto_groups()

    @api.post('/auto-groups')
    async def create_auto_group(payload: AutoGroupRequest):
        try:
            public = payload.model_dump()
            location = public.pop('location')
            return service.store.create_auto_group(location=location,**public)
        except AttendanceError as error: raise HTTPException(409,str(error)) from error

    @api.patch('/auto-groups/{identifier}')
    async def update_auto_group(identifier: str, payload: AutoGroupUpdate):
        try:
            values = payload.model_dump(exclude_unset=True)
            if 'location' in values:
                values['location'] = values['location']
            return service.store.save_auto_group(identifier,**values)
        except KeyError as error: raise HTTPException(404,str(error)) from error
        except AttendanceError as error: raise HTTPException(409,str(error)) from error

    @api.delete('/auto-groups/{identifier}')
    async def delete_auto_group(identifier: str):
        try:
            service.store.delete_auto_group(identifier)
            return {'deleted':True}
        except KeyError as error: raise HTTPException(404,str(error)) from error

    @api.get('/relay')
    async def relay_settings():
        settings_payload = gateway.settings() if hasattr(gateway,'settings') else {}
        hostname = urlsplit(settings_payload.get('base_url') or '').hostname or ''
        token = os.environ.get('MUMU_ATTENDANCE_DOMAIN_TOKEN','').strip()
        return {
            **settings_payload,
            'cname_name':hostname,
            'txt_name':('_mumu-attendance.' + hostname) if hostname and token else '',
            'txt_value':token,
            'multi_domain_ready':bool(settings_payload.get('cname_target') and token),
        }

    async def check_relay(base_url, admin_secret):
        from .relay import RelayClient
        parsed = urlsplit(base_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {'','/'}:
            raise AttendanceError('签到服务地址必须是 HTTPS 域名')
        if len(admin_secret) < 24:
            raise AttendanceError('签到服务管理密钥长度不足')
        client = RelayClient(base_url,admin_secret)
        try: return await client.health()
        finally: await client.close()

    @api.post('/relay/test')
    async def test_relay(payload: RelaySettingsRequest):
        secret = payload.admin_secret or getattr(gateway,'admin_secret','')
        try: return await check_relay(payload.base_url,secret)
        except AttendanceError as error: raise HTTPException(409,str(error)) from error

    @api.put('/relay')
    async def save_relay_settings(payload: RelaySettingsRequest):
        secret = payload.admin_secret or getattr(gateway,'admin_secret','')
        try:
            health = await check_relay(payload.base_url,secret)
            existing_groups = service.store.live_qr_groups()
            previous = getattr(gateway,'base_url','').rstrip('/')
            if existing_groups and previous != payload.base_url.rstrip('/'):
                raise AttendanceError('已有扫码链接绑定时不能直接切换服务域名，请先撤销现有链接')
            service.database_provider().set_setting('attendance_relay',{
                'base_url':payload.base_url.rstrip('/'),'admin_secret':secret,
            })
            if hasattr(gateway,'configure'): await gateway.configure(payload.base_url,secret)
            if hasattr(gateway,'ensure_started'): await gateway.ensure_started()
            return {**health,**(gateway.settings() if hasattr(gateway,'settings') else {})}
        except AttendanceError as error: raise HTTPException(409,str(error)) from error

    return api
