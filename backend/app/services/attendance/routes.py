from __future__ import annotations

import asyncio
from contextlib import contextmanager
import ipaddress
from pathlib import Path
import socket
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .contracts import AttendanceError, SignInput


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    activity_ids: list[str] = Field(min_length=1,max_length=100)


class ShareRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    minutes: int = Field(default=15,ge=1,le=60)
    base_url: str = Field(default='',max_length=2048)


def public_batch(batch):
    return {'id':batch['id'],'status':batch['status'],'completed':batch['completed'],'total':batch['total'],
            'items':[{'id':item['id'],'name':item['account_name'][:1]+'*','course':item['course_name'],
                      'title':item['title'],'kind':item['kind'],'status':item['status'],'message':item['message']} for item in batch['items']]}


def share_app(service):
    app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    recent = {}

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

    @app.get('/s/', response_class=HTMLResponse)
    async def page(): return Path(__file__).with_name('share.html').read_text(encoding='utf-8')

    @app.get('/assets/jsQR.js')
    async def decoder(): return FileResponse(Path(__file__).with_name('jsQR.js'),media_type='application/javascript')

    @app.get('/api/share')
    async def status(request: Request):
        record,_ = authorized(request)
        return {**public_batch(service.store.batch(record['batch_id'])),'expires_at':record['expires_at']}

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

    @api.get('/activities')
    async def activities(): return service.store.activities()

    @api.post('/accounts/{identifier}/collect')
    async def collect(identifier: str):
        try: return await service.collect(identifier)
        except KeyError as error: raise HTTPException(404,'账号不存在') from error
        except (AttendanceError,RuntimeError) as error: raise HTTPException(409,str(error)) from error

    @api.post('/activities/{identifier}/open')
    async def open_sign(identifier: str):
        record = next((row for row in service.store.activities() if row['id']==identifier),None)
        if not record: raise HTTPException(404,'签到活动不存在或已结束')
        if open_activity is None: raise HTTPException(409,'当前环境未提供人工签到浏览器')
        return await open_activity(record)

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

    @api.post('/batches/{identifier}/shares')
    async def create_share(identifier: str, payload: ShareRequest):
        try:
            origin = gateway.origin(payload.base_url)
            service.store.batch(identifier)
            await gateway.ensure_started()
            created = service.store.share(identifier,payload.minutes)
            return {'id':created['id'],'url':origin+'/s/#'+created['token'],'expires_at':created['expires_at']}
        except KeyError as error: raise HTTPException(404,str(error)) from error
        except AttendanceError as error: raise HTTPException(409,str(error)) from error

    @api.delete('/shares/{identifier}')
    async def revoke(identifier: str): service.store.revoke(identifier); return {'revoked':True}
    return api
