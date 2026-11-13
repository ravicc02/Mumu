from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from playwright.async_api import async_playwright

from .browser import LearningBrowser, RUNTIME
from .live import LiveBrowserChannel
from ..runtime.browser import chrome_executable, COURSE_BROWSER_ARGS
from ..accounts.auth import restore_state
from ..platforms.catalog import account_home, platform_spec, platform_url
from ...diagnostics.probe import attach_network, capture_page

STUDY_RUNTIME = Path(__file__).with_name("study-runtime.js").read_text(encoding="utf-8")
PLATFORM_RUNTIME = Path(__file__).with_name("platform-runtime.js").read_text(encoding="utf-8")


class PlatformLearningBrowser(LearningBrowser):
    def __init__(self, account, *, headless=True, authenticate=True):
        super().__init__(account, headless=headless, authenticate=authenticate)
        self.platform = account["platform"]
        self.spec = platform_spec(self.platform)
        self.current_chapter = None
        self.catalog_page = None
        self.resource_pages = set()
        self.navigation_token = None
        self.visited_catalogs = set()
        self.verifier = self.verify_context = self.verify_page = None
        self.verify_state_digest = None

    async def open(self):
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.workspace.path), executable_path=str(chrome_executable()), headless=self.headless,
            no_viewport=not self.headless, viewport={"width":1440,"height":900} if self.headless else None,
            args=COURSE_BROWSER_ARGS + ([] if self.headless else ["--start-maximized"]),
        )
        self._watch_context_lifecycle()
        await restore_state(self.context, self.account["storage_state_path"])
        await self.context.expose_binding("__studioCourseClick", self._navigation_click)
        for source in (RUNTIME, STUDY_RUNTIME, PLATFORM_RUNTIME): await self.context.add_init_script(source)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        attach_network(self.diagnostics, self.context)
        self.live = LiveBrowserChannel(self.page, self.context, resolve_page=self.resolve_live_page)

    async def close(self):
        try:
            if self.verify_context: await self.verify_context.close()
            if self.verifier: await self.verifier.close()
        finally: await super().close()

    async def call(self, frame, method, *args):
        self.ensure_connected()
        for source in (RUNTIME, STUDY_RUNTIME, PLATFORM_RUNTIME): await frame.evaluate(source)
        if method in {"play", "pause", "stop", "readResource", "resourceTabs"}: return await super().call(frame, method, *args)
        if method not in {"snapshot", "catalog", "prepare", "discover"}: raise ValueError("未知平台课程动作")
        return await asyncio.wait_for(frame.evaluate("({method,platform})=>window.__studioCourse[method](platform)", {"method":method,"platform":self.platform}),15)

    async def _catalog_for(self, page, timeout=15, prepare=False):
        deadline = asyncio.get_running_loop().time() + timeout
        prepared = 0
        while True:
            for frame in list(page.frames):
                if frame.is_detached(): continue
                if prepare and prepared < 128:
                    state = await self.call(frame,"prepare")
                    if state.get("expanded"):
                        prepared += 1
                        await asyncio.sleep(.3)
                        continue
                chapters = await self.call(frame,"catalog")
                if chapters:
                    if prepare and self.platform == "yuketang" and prepared < 5:
                        prepared += 1
                        await asyncio.sleep(.2)
                        continue
                    return chapters,frame
            if asyncio.get_running_loop().time()>=deadline: raise RuntimeError(f"未读取到{self.spec['label']}课程目录，请确认已进入课程学习页")
            if any(token in urlsplit(page.url).path.lower() for token in ("/login","/passport","/signin")):
                raise RuntimeError(f"{self.spec['label']}登录状态已失效，请重新授权")
            await asyncio.sleep(.5)

    async def discover_courses(self, year):
        await self.page.goto(account_home(self.account),wait_until="domcontentloaded",timeout=45000)
        if any(token in urlsplit(self.page.url).path.lower() for token in ("/login","/passport","/signin")):
            raise RuntimeError("平台登录状态已失效，请重新授权")
        deadline=asyncio.get_running_loop().time()+15
        while True:
            result=[]
            for frame in list(self.page.frames): result.extend(await self.call(frame,"discover"))
            unique={}
            for item in result:
                try: platform_url(self.platform,item["url"])
                except ValueError: continue
                unique[(item["courseId"],item["classId"])]=item
            if unique: return list(unique.values())
            if asyncio.get_running_loop().time()>=deadline:
                raise RuntimeError("当前账号页面未发现课程链接，可使用加入课程添加平台中的课程学习链接")
            await asyncio.sleep(.5)

    async def navigate(self, url):
        if self.authenticate: platform_url(self.platform,url)
        await self.page.goto(url,wait_until="domcontentloaded",timeout=45000)
        if self.authenticate: platform_url(self.platform,self.page.url)
        self.catalog_page=self.page
        chapters,self.catalog_frame=await self._catalog_for(self.catalog_page,prepare=True)
        self.catalog_url=self.catalog_page.url
        self.diagnostics.snapshot("chapter-catalog",{"platform":self.platform,"url":self.catalog_url,"chapters":chapters})
        await capture_page(self.diagnostics,self.catalog_frame,"platform-catalog")

    async def catalog(self,timeout=15):
        chapters,self.catalog_frame=await self._catalog_for(self.catalog_page or self.page,timeout,prepare=True)
        return chapters

    async def verify_catalog(self):
        state=await self.context.storage_state()
        state_digest=hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()
        if self.verifier is None:
            self.verifier=await self.playwright.chromium.launch(headless=True,executable_path=str(chrome_executable()),args=COURSE_BROWSER_ARGS)
        if self.verify_context is None or state_digest!=self.verify_state_digest:
            fresh=await self.verifier.new_context(storage_state=state)
            try: page=await fresh.new_page()
            except BaseException:
                await fresh.close()
                raise
            previous=self.verify_context
            self.verify_context,self.verify_page=fresh,page
            self.verify_state_digest=state_digest
            if previous: await previous.close()
        # Read a fresh platform page without refreshing the playing page or reusing stale tokens.
        await self.verify_page.goto(self.catalog_url,wait_until="domcontentloaded",timeout=45000)
        if self.authenticate: platform_url(self.platform,self.verify_page.url)
        chapters,_=await self._catalog_for(self.verify_page,timeout=15,prepare=True)
        expected,actual=urlsplit(self.catalog_url),urlsplit(self.verify_page.url)
        identity_keys={"courseid","course_id","classid","class_id","classroomid","cid","id"}
        expected_ids={key.lower():value for key,value in parse_qs(expected.query).items() if key.lower() in identity_keys}
        actual_ids={key.lower():value for key,value in parse_qs(actual.query).items() if key.lower() in identity_keys}
        if expected.path!=actual.path or expected.fragment!=actual.fragment or any(actual_ids.get(key)!=value for key,value in expected_ids.items()):
            raise RuntimeError("平台核验页面的课程标识与当前课程不一致")
        signature=json.dumps(chapters,sort_keys=True)
        if signature!=self.catalog_signature:
            self.catalog_signature=signature
            self.diagnostics.record("learning.catalog_verified",data={"platform":self.platform,"url":self.verify_page.url,"verification":"fresh_authorized_page","chapters":chapters},component="learning_browser")
        return chapters

    async def open_chapter(self,identifier):
        async with self.io_lock:
            catalog_page=self.catalog_page or self.page
            if catalog_page.is_closed(): raise RuntimeError("课程目录窗口已关闭，请重新打开课程")
            chapters,self.catalog_frame=await self._catalog_for(catalog_page,prepare=True)
            before=set(self.context.pages)
            target=next((item for item in chapters if item["chapterId"]==identifier),None)
            if not target: raise RuntimeError("平台章节已变化，请重新采集课程")
            self.current_chapter=target
            if target.get("navigable"):
                await self.catalog_frame.locator('[data-studio-chapter='+json.dumps(identifier)+']').click(timeout=10000)
            elif self.platform=="zjy":
                await self.catalog_frame.evaluate("({id})=>{ const vue=window.__mumuStudyRuntime.zjy.getVueBindElement(); if(!vue?.nextObj||String(vue.nextObj.id)!==id) throw new Error('该章节缺少可操作入口'); vue.preNext(vue.nextObj); }",{"id":identifier})
            else: raise RuntimeError("该章节尚未提供可操作入口，请在浏览器选择章节")
        await asyncio.sleep(1)
        available=[]
        for page in self.context.pages:
            if page in before or page.is_closed(): continue
            await page.wait_for_load_state("domcontentloaded",timeout=15000)
            if self.authenticate:
                try: platform_url(self.platform,page.url)
                except ValueError: continue
            available.append(page)
        self.page=available[-1] if available else catalog_page
        self.resource_pages.update(available)
        for resource in list(self.resource_pages):
            if resource is not self.page:
                if not resource.is_closed(): await resource.close()
                self.resource_pages.discard(resource)

    async def _navigation_click(self, source, payload):
        if not self.navigation_token or payload.get("token") != self.navigation_token:
            raise RuntimeError("课程导航操作不属于当前请求")
        frame=source["frame"]
        if source["page"] not in {self.page,self.catalog_page}: raise RuntimeError("课程导航页面不一致")
        identifier=payload.get("id","")
        if not isinstance(identifier,str) or not identifier.startswith("nav-") or len(identifier)>32:
            raise ValueError("课程导航标记无效")
        target=frame.locator('[data-studio-navigation='+json.dumps(identifier)+']')
        allowed=await target.evaluate("el=>!el.closest('form') && !/提交|交卷|删除|付款/.test(el.textContent) && !!el.closest('.unitslist,.j-lesson,.j-chapter,header,.learn-unit-tabs-scroll')")
        if not allowed: raise RuntimeError("课程导航目标不在允许范围内")
        await target.click(timeout=10000)
        await asyncio.sleep(.25)

    async def advance_catalog(self):
        if self.platform!="icourse": return False
        chapters,frame=await self._catalog_for(self.catalog_page or self.page)
        signature=(self.catalog_page or self.page).url+":"+",".join(item["chapterId"] for item in chapters)
        current=await frame.evaluate("()=>document.querySelector('.unitslist .current,.learn-unit-tabs-scroll button[class*=rgba]')?.textContent||''")
        key=signature+":"+current
        if key in self.visited_catalogs: raise RuntimeError("课程导航没有推进，请在浏览器检查目录")
        self.visited_catalogs.add(key)
        if len(self.visited_catalogs)>1000: raise RuntimeError("课程导航超过本次执行上限")
        self.navigation_token=secrets.token_urlsafe(24)
        try:
            result=await frame.evaluate("({platform,token})=>window.__studioCourse.advance(platform,token)",{"platform":self.platform,"token":self.navigation_token})
        finally: self.navigation_token=None
        if not result["advanced"]: return False
        await asyncio.sleep(1)
        self.catalog_url=(self.catalog_page or self.page).url
        return True

    async def inspect(self):
        items,attachments=[],{}
        async with self.io_lock:
            for frame in list(self.page.frames):
                if frame.is_detached(): continue
                if frame.parent_frame and not await (await frame.frame_element()).is_visible(): continue
                data=await self.call(frame,"snapshot")
                media=data.get("media")
                source=media.get("source","") if media else ""
                chapter=self.current_chapter or {"chapterId":"unknown","title":"课程任务"}
                key=chapter["chapterId"]+":"+hashlib.sha256((frame.url+source).encode()).hexdigest()[:12]
                if media:
                    attachments[key]={"id":key,"title":chapter["title"],"mandatory":True,"completed":False}
                items.append({"frame":frame,"data":data,"job_id":key})
        return attachments,items
