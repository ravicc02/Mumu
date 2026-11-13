from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from playwright.async_api import Error as PlaywrightError, async_playwright

from ..assignments import chaoxing
from ..runtime.browser import chrome_executable, COURSE_BROWSER_ARGS, TaskBrowserProfile
from ..runtime.screenshots import capture_screenshot_segments
from .live import LiveBrowserChannel
from ...diagnostics.recorder import NullTrace
from ...diagnostics.probe import attach_network, capture_page as capture_diagnostic_page

RUNTIME = Path(__file__).with_name("runtime.js").read_text(encoding="utf-8")
STUDY_RUNTIME = Path(__file__).with_name("study-runtime.js").read_text(encoding="utf-8")


def create_learning_browser(account, **options):
    if account["platform"] == "chaoxing": return LearningBrowser(account, **options)
    from .multi_platform import PlatformLearningBrowser
    return PlatformLearningBrowser(account, **options)


class LearningBrowser:
    def __init__(self, account, *, headless=True, authenticate=True):
        self.account = account
        self.workspace = TaskBrowserProfile(account, "learning", account.get("_task_id", ""))
        self.headless = headless
        self.authenticate = authenticate
        self.playwright = None
        self.context = None
        self.page = None
        self.catalog_frame = None
        self.catalog_url = ""
        self.expected_ids = {}
        self.io_lock = asyncio.Lock()
        self.live = None
        self.diagnostics = NullTrace()
        self.catalog_signature = ""
        self.catalog_page = None
        self.resource_pages = set()
        self.connection_lost = asyncio.Event()
        self.close_reason = ""
        self.failure_kind = ""
        self._closing = False
        self.visited_resource_tabs = set()
        self.frame_errors = set()

    async def open(self):
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.workspace.path), executable_path=str(chrome_executable()), headless=self.headless,
            no_viewport=not self.headless, viewport={"width": 1440, "height": 900} if self.headless else None,
            args=COURSE_BROWSER_ARGS + ([] if self.headless else ["--start-maximized"]),
        )
        self._watch_context_lifecycle()
        await self.context.add_init_script(STUDY_RUNTIME)
        await self.context.add_init_script(RUNTIME)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        attach_network(self.diagnostics, self.context)
        if self.authenticate:
            await chaoxing._restore_storage_state(self.context, self.account["storage_state_path"])
            if not await chaoxing._verify_authenticated(self.context):
                raise RuntimeError("学习通登录态已失效，请刷新 Cookie")
        self.live = LiveBrowserChannel(self.page, self.context, resolve_page=self.resolve_live_page)

    def _lost(self, kind: str):
        if self._closing or self.connection_lost.is_set(): return
        self.failure_kind = kind
        self.close_reason = ("网课页面崩溃，任务已中断，请重试。" if kind == "page_crashed" else
            "后台浏览器连接已断开，任务已中断。请重试以重新读取平台进度。" if self.headless else
            "浏览器窗口已关闭，任务已中断。请重试以重新读取平台进度。")
        self.connection_lost.set()

    def _watch_context_lifecycle(self):
        self.context.on("close", lambda *_: self._lost("context_closed"))
        if self.context.browser:
            self.context.browser.on("disconnected", lambda *_: self._lost("browser_disconnected"))
        def watch_page(page):
            page.on("crash", lambda *_: self._lost("page_crashed"))
            def closed(*_):
                if page is self.page or page is self.catalog_page:
                    self._lost("active_page_closed")
                elif not any(not item.is_closed() for item in self.context.pages): self._lost("last_page_closed")
            page.on("close", closed)
        for page in self.context.pages: watch_page(page)
        self.context.on("page", watch_page)

    def ensure_connected(self):
        if self.connection_lost.is_set(): raise RuntimeError(self.close_reason)

    async def resolve_live_page(self):
        self.ensure_connected()
        if self.page is None or self.page.is_closed():
            available=[page for page in self.context.pages if not page.is_closed()]
            if not available: raise RuntimeError("任务浏览器已关闭")
            self.page=available[-1]
        return self.page,self.context

    async def close(self):
        try:
            if self.live: await self.live.close()
        finally:
            await self._close_browser()

    async def _close_browser(self):
        self._closing = True
        try:
            if self.context: await asyncio.wait_for(self.context.close(), 8)
        finally:
            try:
                if self.playwright: await asyncio.wait_for(self.playwright.stop(), 8)
            finally:
                self.workspace.close()

    async def discover_courses(self, year):
        url, html = await chaoxing._fetch_rendered_page(self.page, "https://i.chaoxing.com/base?ws=1", 1000)
        document = BeautifulSoup(html, "html.parser")
        frame = document.select_one("iframe#frame_content") or document.select_one("iframe[src*='/visit/interaction']")
        if not frame or not frame.get("src"): raise RuntimeError("登录状态未通过课程中心核验，请刷新 Cookie")
        target = chaoxing._normalize_course_list_url(urljoin(url, frame["src"]))
        url, html = await chaoxing._fetch_student_course_list(self.page, target)
        courses = [course for course in chaoxing._parse_courses(html, url) if course["startDate"].startswith(f"{year}-") and not course["ended"]]
        if not courses: raise RuntimeError(f"没有找到 {year} 年开课且仍开放的课程")
        return [{**course, "semester": chaoxing._semester_for(course["startDate"])} for course in courses]

    async def navigate(self, url):
        if self.authenticate:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not any((parsed.hostname or "").endswith(host) for host in (".chaoxing.com", ".chaoxing.cn")):
                raise RuntimeError("课程地址不属于已支持的学习通域名")
            self.expected_ids = {key.lower(): values[0] for key, values in parse_qs(parsed.query).items() if key.lower() in {"courseid", "clazzid"}}
        await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if "passport" in self.page.url: raise RuntimeError("学习通登录态已失效，请刷新 Cookie")
        if "/mycourse/stu" in self.page.url:
            await self.page.get_by_role("link", name="章节", exact=True).click(timeout=15000)
        await self.catalog()

    async def call(self, frame, method, *args):
        self.ensure_connected()
        if method not in {"snapshot", "play", "pause", "stop", "openChapter", "parseCatalog", "readResource", "resourceTabs"}: raise ValueError("未知课程动作")
        await frame.evaluate(STUDY_RUNTIME)
        await frame.evaluate(RUNTIME)
        timeout = max(15, args[0].get("resource_wait_seconds", 15) + 2) if method == "play" and args else 15
        if method == "readResource": timeout = 180
        return await asyncio.wait_for(frame.evaluate("async ({method,args}) => await window.__studioLearning[method](...args)", {"method": method, "args": args}), timeout)

    async def catalog(self, timeout=15):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            empty = False
            for frame in list(self.page.frames):
                try:
                    data = await self.call(frame, "snapshot")
                    if data["chapters"]:
                        self.catalog_frame, self.catalog_url = frame, frame.url
                        self.catalog_page = self.page
                        self.diagnostics.snapshot("chapter-catalog", {"url": frame.url, "chapters": data["chapters"]})
                        await capture_diagnostic_page(self.diagnostics, frame, "catalog-controls")
                        return data["chapters"]
                    empty = empty or await frame.get_by_text("暂无章节内容", exact=True).is_visible()
                except Exception:
                    if self.page.is_closed(): raise RuntimeError("网课浏览器已关闭")
            if asyncio.get_running_loop().time() >= deadline:
                if empty: raise ValueError("该课程暂无章节内容；课程作业仍可在平台页处理")
                raise RuntimeError("未读取到学习通章节目录，当前课程页面尚不受支持或加载失败")
            await asyncio.sleep(.5)

    async def verify_catalog(self):
        response = await self.context.request.get(self.catalog_url, timeout=20000)
        if response.status in {401, 403} or "passport" in response.url: raise RuntimeError("平台登录状态已失效，请重新授权")
        if not response.ok: raise RuntimeError(f"平台进度核验失败 HTTP {response.status}")
        if self.expected_ids:
            actual = {key.lower(): values[0] for key, values in parse_qs(urlsplit(response.url).query).items()}
            if any(actual.get(key) != value for key, value in self.expected_ids.items()):
                raise RuntimeError("平台响应的课程或班级与当前任务不一致，未确认完成")
        chapters = await self.call(self.page.main_frame, "parseCatalog", await response.text())
        if not chapters: raise RuntimeError("平台核验响应没有章节目录，不能确认完成")
        signature = json.dumps(chapters, sort_keys=True)
        if signature != self.catalog_signature:
            self.catalog_signature = signature
            self.diagnostics.record("learning.catalog_verified", data={"url": response.url, "http_status": response.status, "chapters": chapters}, component="learning_browser")
        return chapters

    async def open_chapter(self, identifier):
        async with self.io_lock:
            self.visited_resource_tabs.clear()
            self.ensure_connected()
            catalog = self.catalog_page or self.page
            if catalog.is_closed(): raise RuntimeError("课程目录页面已关闭，请重试任务")
            if self.catalog_frame.is_detached() or self.catalog_frame.url != self.catalog_url:
                self.page = catalog
                await catalog.goto(self.catalog_url, wait_until="domcontentloaded", timeout=45000)
                await self.catalog()
            before = set(self.context.pages)
            await self.call(self.catalog_frame, "snapshot")
            selector = '[data-studio-learning-chapter=' + json.dumps(str(identifier)) + ']'
            await self.catalog_frame.locator(selector).click(timeout=10000)
            await asyncio.sleep(1.5)
            opened = [page for page in self.context.pages if page not in before and not page.is_closed()]
            if opened:
                target = opened[-1]
                await target.wait_for_load_state("domcontentloaded", timeout=15000)
                if self.authenticate:
                    from ..platforms.catalog import platform_url
                    platform_url(self.account["platform"], target.url)
                self.page = target
                self.resource_pages.update(opened)
            else:
                self.page = catalog
            for old in list(self.resource_pages):
                if old is not self.page:
                    if not old.is_closed(): await old.close()
                    self.resource_pages.discard(old)
            self.diagnostics.record("learning.chapter_page", "Chapter page selected", data={"chapter_id": identifier, "url": self.page.url, "new_page": bool(opened)})

    async def inspect(self):
        items = []
        attachments = {}
        async with self.io_lock:
            for frame in list(self.page.frames):
                try:
                    if frame.parent_frame and not await (await frame.frame_element()).is_visible(): continue
                    data = await self.call(frame, "snapshot")
                    for attachment in data["attachments"]: attachments[attachment["id"]] = attachment
                    job_id = ""
                    parent = frame
                    for _ in range(6):
                        if not parent.parent_frame: break
                        element = await parent.frame_element()
                        raw = await element.get_attribute("data")
                        if raw:
                            metadata = json.loads(raw)
                            job_id = str(metadata.get("jobid") or metadata.get("_jobid") or "")
                            if job_id: break
                        parent = parent.parent_frame
                    items.append({"frame": frame, "data": data, "job_id": job_id})
                except Exception as error:
                    if self.page.is_closed(): raise RuntimeError("网课浏览器已关闭")
                    key = (frame.url, type(error).__name__)
                    if key not in self.frame_errors:
                        self.frame_errors.add(key)
                        self.diagnostics.record("learning.frame_inspection_failed", str(error), {"url": frame.url}, level="warning")
        return attachments, items

    async def read_resource(self, frame):
        async with self.io_lock:
            return await self.call(frame, "readResource")

    async def advance_resource_card(self):
        async with self.io_lock:
            for frame in list(self.page.frames):
                if frame.is_detached(): continue
                tabs = await self.call(frame, "resourceTabs")
                target = tabs.get("next")
                key = (frame.url, target)
                if target is None or key in self.visited_resource_tabs: continue
                self.visited_resource_tabs.add(key)
                await frame.locator('[data-studio-resource-tab="' + str(target) + '"]').click(timeout=10000)
                self.diagnostics.record("learning.resource_tab", data={"index": target, "count": tabs["count"]})
                await asyncio.sleep(.5)
                return True
            return False

    async def play(self, frame):
        async with self.io_lock:
            before = await self.call(frame, "snapshot")
            if before.get("challenge") or before.get("quiz") or before.get("error"):
                return {"started": False, "blocked": True, "reason": "播放器存在验证、练习或加载错误"}
            result = await self.call(frame, "play", getattr(self, "learning_options", {}))
            if result.get("started") or result.get("cancelled"): return result
            state = await self.call(frame, "snapshot")
            if state.get("generation") != result.get("generation"): return {**result, "cancelled": True}
            if state.get("challenge") or state.get("quiz") or state.get("error"):
                return {**result, "blocked": True}
            media = state.get("media")
            if not media or not media["paused"] or media["ended"]:
                return {**result, "started": bool(media), "adopted": bool(media)}
            buttons = frame.locator('.vjs-big-play-button, .vjs-play-control.vjs-paused, '
                'button[aria-label="Play"], button[aria-label="播放"], button[title="播放"], '
                '.plyr__control[data-plyr="play"]:not(.plyr__control--pressed)')
            for index in range(min(await buttons.count(), 12)):
                button = buttons.nth(index)
                if not await button.is_visible() or not await button.is_enabled(): continue
                latest = await self.call(frame, "snapshot")
                if latest.get("generation") != result.get("generation"): return {**result, "cancelled": True}
                if latest.get("challenge") or latest.get("quiz") or latest.get("error"):
                    return {**result, "blocked": True}
                media = latest.get("media")
                if not media or not media["paused"] or media["ended"]:
                    return {**result, "started": bool(media), "adopted": bool(media)}
                try:
                    await button.click(timeout=1500)
                    await asyncio.sleep(.3)
                    after = await self.call(frame, "snapshot")
                    if after.get("generation") != result.get("generation"):
                        await self.call(frame, "pause")
                        return {**result, "started": False, "cancelled": True}
                    media = after.get("media")
                    started = bool(media and (not media["paused"] or media["ended"]))
                    self.diagnostics.record("learning.play_button", data={"started": started, "url": frame.url})
                    return {**result, "started": started, "method": "play_button"}
                except PlaywrightError as error:
                    self.ensure_connected()
                    return {**result, "click_error": str(error)}
            return result

    async def pause(self):
        # Cancellation must reach an in-flight reader before waiting for its I/O lock.
        for frame in list(self.page.frames):
            if frame.is_detached(): continue
            try:
                result = await self.call(frame, "pause")
                if not result.get("paused"): raise RuntimeError("播放器尚未暂停")
            except Exception:
                if not frame.is_detached(): raise

    async def focus(self):
        self.ensure_connected()
        if self.headless: raise RuntimeError("该任务在后台运行，请通过软件内的实时画面操控")
        await self.page.bring_to_front()

    async def snapshot(self):
        image = await self.page.screenshot(type="jpeg", quality=65, timeout=10000)
        return {"image": "data:image/jpeg;base64," + base64.b64encode(image).decode(), "url": self.page.url}

    async def evidence(self):
        client = await self.context.new_cdp_session(self.page)
        async def capture(params, session_id): return await client.send("Page.captureScreenshot", params)
        async def metrics(session_id): return await client.send("Page.getLayoutMetrics")
        session = SimpleNamespace(
            get_current_page=lambda: asyncio.sleep(0, result=SimpleNamespace(session_id="learning")),
            get_current_page_url=lambda: asyncio.sleep(0, result=self.page.url),
            cdp_client=SimpleNamespace(send=SimpleNamespace(Page=SimpleNamespace(captureScreenshot=capture, getLayoutMetrics=metrics))),
        )
        try:
            async for section in capture_screenshot_segments(session): yield section
        finally:
            await client.detach()
