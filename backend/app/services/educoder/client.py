from __future__ import annotations

import asyncio
import time
import logging
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
from ..runtime.browser import chrome_executable
from .contracts import ContractError, identifier, site_url


API_ORIGIN = "https://data.educoder.net"
HEADERS = {"pc-authorization", "x-edu-type", "x-edu-timestamp", "x-edu-signature"}
logger = logging.getLogger("mumu.educoder")


class EduCoderClient:
    """Observe fresh headers from the site itself; never embed its signing keys."""
    def __init__(self, account, *, page=None):
        self.account, self.page = account, page
        self.context = page.context if page else None
        self.playwright = self.browser = None
        self.headers, self.observed_at = {}, 0
        self.header_ready = asyncio.Event()
        self.observers = set()
        self.closing = False

    def _observe(self, request):
        if self.closing: return
        task = asyncio.create_task(self._headers(request))
        self.observers.add(task)
        task.add_done_callback(self.observers.discard)

    async def _headers(self, request):
        if not request.url.startswith(API_ORIGIN + "/api/"): return
        try:
            headers = {k.lower(): v for k,v in (await asyncio.wait_for(request.all_headers(), 5)).items() if k.lower() in HEADERS}
            if HEADERS <= headers.keys():
                self.headers, self.observed_at = headers, time.monotonic()
                self.header_ready.set()
        except Exception:
            return

    async def __aenter__(self):
        from ..accounts.auth import restore_state
        attached = self.page is not None
        if self.page is None:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(executable_path=str(chrome_executable()), headless=True)
            self.context = await self.browser.new_context(viewport={"width":1440,"height":900})
            await restore_state(self.context, self.account["storage_state_path"])
            self.page = await self.context.new_page()
        self.context.on("request", self._observe)
        auth_page = None
        try:
            auth_page = await self.context.new_page() if attached else self.page
            await auth_page.goto("https://www.educoder.net/",wait_until="domcontentloaded",timeout=45000)
            await asyncio.wait_for(self.header_ready.wait(),20)
        except BaseException as error:
            await self.__aexit__(None,None,None)
            if isinstance(error,(asyncio.TimeoutError,BrowserTimeout)):
                raise ContractError("头歌页面未能完成会话初始化，请检查网络或重新授权。") from error
            raise
        finally:
            if attached and auth_page is not None:
                await auth_page.close()
        return self

    async def __aexit__(self, *args):
        self.closing = True
        if self.context: self.context.remove_listener("request",self._observe)
        observers = tuple(self.observers)
        for task in observers:
            if not task.done(): task.cancel()
        if observers:
            _, pending = await asyncio.wait(observers, timeout=2)
            if pending: logger.warning("Header observer cleanup reached its deadline: pending=%s", len(pending))
        self.observers.clear()
        for resource, method in ((self.browser, "close"), (self.playwright, "stop")):
            if resource:
                try: await asyncio.wait_for(getattr(resource, method)(), 5)
                except Exception as error: logger.warning("EduCoder client cleanup failed: %s", type(error).__name__)

    async def request(self, path, *, method="GET", params=None, data=None):
        if not path.startswith("/api/") or ".." in path or "?" in path or "#" in path or ":" in path:
            raise ContractError("拒绝无效的头歌接口路径。")
        if time.monotonic()-self.observed_at > 45:
            self.header_ready.clear()
            refresh_page = await self.context.new_page()
            try:
                await refresh_page.goto("https://www.educoder.net/",wait_until="domcontentloaded",timeout=30000)
                await asyncio.wait_for(self.header_ready.wait(),15)
            except asyncio.TimeoutError as error: raise ContractError("头歌会话请求头未刷新，请重新打开任务或重新授权。") from error
            finally: await refresh_page.close()
        response = await self.context.request.fetch(API_ORIGIN+path, method=method, params=params, data=data,
            headers={**self.headers,"Origin":"https://www.educoder.net","Referer":"https://www.educoder.net/","Content-Type":"application/json"},
            timeout=30000, max_redirects=0)
        if response.status in {401,403}: raise ContractError("头歌授权或访问权限已失效，请重新授权。")
        if not response.ok: raise ContractError(f"头歌接口返回 HTTP {response.status}，请导出诊断包。")
        try: payload = await response.json()
        except Exception as error: raise ContractError("头歌接口没有返回 JSON，未将其当成成功结果。") from error
        pending_evaluation = method == "GET" and path.endswith("/game_status.json") and isinstance(payload,dict) and payload.get("status") == -1
        if isinstance(payload,dict) and isinstance(payload.get("status"),int) and payload["status"] in {-1,401,403} and not pending_evaluation:
            raise ContractError(str(payload.get("message") or "头歌拒绝了当前操作。"))
        return payload

    async def identity(self):
        data = await self.request("/api/users/get_user_info.json")
        if not isinstance(data,dict) or not data.get("login") or not (data.get("user_id") or data.get("id")):
            raise ContractError("头歌登录尚未完成或登录态已失效，请在授权窗口完成登录。")
        login = identifier(data.get("login"))
        uid = identifier(data.get("user_id") or data.get("id"))
        name = str(data.get("username") or data.get("real_name") or "").strip()
        if not name: raise ContractError("头歌已登录，但平台显示名称尚未读取成功。")
        return {"platform_user_id":uid,"username":login,"name":name[:100],"avatar_url":str(data.get("image_url") or "")}

    async def all_pages(self, path, key, params=None):
        from .contracts import paged
        records, seen, expected_total = [], set(), None
        for page in range(1,501):
            payload = await self.request(path,params={**(params or {}),"page":page,"per_page":50})
            items,total = paged(payload,key)
            if expected_total is None: expected_total = total
            elif total != expected_total: raise ContractError("头歌分页查询总数发生变化，请重新采集。")
            if not items and len(records)<total: raise ContractError("分页提前返回空列表，采集未完成。")
            for item in items:
                key_id=identifier(item.get("homework_id") or item.get("id"))
                if key_id in seen: raise ContractError("头歌分页重复，已停止采集以免漏项。")
                seen.add(key_id); records.append(item)
            if len(records)==total: return records
            if len(records)>total: raise ContractError("头歌分页总数发生变化，请重新采集。")
        raise ContractError("课堂数据超出单次采集上限。")
