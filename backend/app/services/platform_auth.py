from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from ..db import utc_now
from .browser import chrome_executable
from .platforms.catalog import account_home, platform_url

_jobs, _statuses, _sessions = {}, {}, {}


def login_in_progress(identifier):
    return identifier in _jobs and not _jobs[identifier].done()


def login_status(identifier): return _statuses.get(identifier, {"status": "idle"})


async def shutdown():
    tasks=list(_jobs.values())
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)


async def restore_state(context, filename):
    path = Path(filename)
    if not path.is_file(): return
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("cookies"): await context.add_cookies(state["cookies"])
    origins = state.get("origins", [])
    if origins:
        await context.add_init_script("(() => { const origins=" + json.dumps(origins) + "; const item=origins.find(item=>item.origin===location.origin); if(item) for(const field of item.localStorage||[]) localStorage.setItem(field.name,field.value); })();")


async def authenticated(page):
    if any(word in urlsplit(page.url).path.lower() for word in ("/login", "/passport", "/signin")): return False
    return await page.evaluate("""() => {
      const visible=e=>!!e?.getClientRects().length;
      if([...document.querySelectorAll('input[type=password]')].some(visible)) return false;
      return [...document.querySelectorAll('[data-user-id],.user-info .user-name,.userInfo .userName,.u-navLogin-container .userName,.student-info,.user-name')].some(e=>visible(e)&&e.textContent.trim());
    }""")


async def authenticated_for_platform(platform, page):
    try: platform_url(platform, page.url)
    except ValueError: return False
    if platform == "educoder":
        return await page.locator('a[href*="/messages/"][href*="/user_tidings"]').count() > 0
    return await authenticated(page)


def start_login(database, identifier, settings):
    if login_in_progress(identifier): return {"status": "running"}
    database.update_account(identifier, status="authorizing", last_login_error=None)
    _statuses[identifier] = {"status": "starting"}
    _jobs[identifier] = asyncio.create_task(_login(database, identifier))
    return _statuses[identifier]


async def confirm_login(database, identifier):
    session = _sessions.get(identifier)
    if not session: raise RuntimeError("授权窗口尚未就绪，请先打开授权浏览器")
    account = database.get_account(identifier)
    page = next((page for page in reversed(session["context"].pages) if not page.is_closed()), None)
    if not page: raise RuntimeError("授权浏览器已关闭，请重新授权")
    platform_url(account["platform"], page.url)
    if any(word in urlsplit(page.url).path.lower() for word in ("/login", "/passport", "/signin")):
        raise RuntimeError("当前仍为登录页面，请先完成平台登录")
    if await page.locator('input[type="password"]:visible').count(): raise RuntimeError("当前仍显示登录表单")
    session["confirmed"].set()
    return {"status": "verifying", "message": "正在保存浏览器授权"}


async def _login(database, identifier):
    account = database.get_account(identifier)
    context = None
    try:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(account["profile_dir"], executable_path=str(chrome_executable()), headless=False, no_viewport=True, args=["--start-maximized"])
            await restore_state(context, account["storage_state_path"])
            page = context.pages[0] if context.pages else await context.new_page()
            confirmed = asyncio.Event()
            _sessions[identifier] = {"context": context, "confirmed": confirmed}
            await page.goto(account_home(account), wait_until="domcontentloaded", timeout=45000)
            _statuses[identifier] = {"status": "waiting", "message": "请在浏览器完成登录，再点击已完成登录"}
            deadline = asyncio.get_running_loop().time() + 300
            while not confirmed.is_set():
                if asyncio.get_running_loop().time() > deadline: raise RuntimeError("授权等待超时，请重新授权")
                if not context.pages: raise RuntimeError("授权浏览器已关闭")
                if any([await authenticated_for_platform(account["platform"], item) for item in context.pages if not item.is_closed()]): break
                await asyncio.sleep(1)
            from .account_identity import save_identity
            await save_identity(database,identifier,page)
            await context.storage_state(path=account["storage_state_path"])
            database.update_account(identifier, status="ready", last_login_at=utc_now(), last_login_error=None)
            _statuses[identifier] = {"status": "success", "message": "浏览器授权已保存"}
            await context.close(); context = None
    except asyncio.CancelledError:
        database.update_account(identifier,status="login_error",last_login_error="授权被应用关闭中断，请重新授权")
        _statuses[identifier]={"status":"error","message":"授权已中断，请重新授权"}
        raise
    except Exception as error:
        database.update_account(identifier, status="login_error", last_login_error=str(error))
        _statuses[identifier] = {"status": "error", "message": str(error)}
    finally:
        _sessions.pop(identifier, None)
        _jobs.pop(identifier, None)
        if context:
            try: await context.close()
            except Exception: pass


async def refresh_session(database, identifier):
    account = database.get_account(identifier)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(account["profile_dir"], executable_path=str(chrome_executable()), headless=True)
        try:
            await restore_state(context, account["storage_state_path"])
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(account_home(account), wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)
            if not await authenticated_for_platform(account["platform"], page):
                database.update_account(identifier,status="login_error",last_login_error="未能核验授权，请重新打开浏览器完成授权")
                return {"ok": False, "status": "login_error", "message": "未能核验授权，请重新打开浏览器完成授权"}
            from .account_identity import save_identity
            await save_identity(database,identifier,page)
            await context.storage_state(path=account["storage_state_path"])
            database.update_account(identifier, status="ready", last_login_at=utc_now(), last_login_error=None)
            return {"ok": True, "status": "ready", "message": "授权已核验并保存"}
        finally: await context.close()
