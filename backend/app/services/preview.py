from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from ..db import Database
from .browser import chrome_executable, COURSE_BROWSER_ARGS, TaskBrowserProfile
from .platforms.catalog import account_home
from .platform_auth import restore_state


class ProfilePreviewManager:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}
        self._interactive_accounts: set[str] = set()
        self._lock = asyncio.Lock()
        self._workspaces: dict[str, tuple[BrowserContext, TaskBrowserProfile]] = {}

    def _forget_locked(
        self,
        account_id: str,
        *,
        context: BrowserContext | None = None,
        page: Page | None = None,
    ) -> None:
        if context is not None and self._contexts.get(account_id) is not context:
            return
        if page is not None and self._pages.get(account_id) is not page:
            return
        self._interactive_accounts.discard(account_id)
        self._pages.pop(account_id, None)
        self._contexts.pop(account_id, None)

    async def _handle_page_close(
        self,
        account_id: str,
        context: BrowserContext,
        page: Page,
    ) -> None:
        async with self._lock:
            if (
                self._contexts.get(account_id) is not context
                or self._pages.get(account_id) is not page
            ):
                return
            self._forget_locked(account_id, context=context, page=page)
            try:
                await context.close()
            except Exception:
                pass

    async def _handle_context_close(
        self,
        account_id: str,
        context: BrowserContext,
    ) -> None:
        async with self._lock:
            self._forget_locked(account_id, context=context)
            owned = self._workspaces.get(account_id)
            if owned and owned[0] is context:
                self._workspaces.pop(account_id, None)
                owned[1].close()

    async def _ensure_browser(self) -> Browser:
        if self._browser and self._browser.is_connected():
            return self._browser
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            executable_path=str(chrome_executable()),
        )
        return self._browser

    async def screenshot(
        self,
        database: Database,
        account_id: str,
        reload: bool = False,
    ) -> dict[str, Any]:
        async with self._lock:
            account = database.get_account(account_id)
            browser = await self._ensure_browser()
            context = self._contexts.get(account_id)
            if context is None or not context.browser.is_connected():
                storage_state = account["storage_state_path"]
                context_options: dict[str, Any] = {
                    "viewport": {"width": 1280, "height": 820},
                    "device_scale_factor": 1,
                }
                if Path(storage_state).is_file():
                    context_options["storage_state"] = storage_state
                context = await browser.new_context(**context_options)
                self._contexts[account_id] = context
            page = self._pages.get(account_id)
            if page is None or page.is_closed():
                page = await context.new_page()
                self._pages[account_id] = page
                reload = True
            if reload or page.url == "about:blank":
                await page.goto(
                    account_home(account),
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await page.wait_for_timeout(1200)
            image = await page.screenshot(
                type="jpeg",
                quality=58,
                full_page=False,
            )
            return {
                "image": "data:image/jpeg;base64,"
                + base64.b64encode(image).decode("ascii"),
                "url": page.url,
                "title": await page.title(),
                "captured_at": asyncio.get_running_loop().time(),
            }

    async def open_interactive(
        self, database: Database, account_id: str, target_url: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            account = database.get_account(account_id)
            key = f"{account_id}:assignment:{task_id}" if task_id else account_id
            destination = target_url or account_home(account)
            existing_page = self._pages.get(key)
            if key in self._interactive_accounts and existing_page and not existing_page.is_closed():
                if existing_page.url != destination:
                    await existing_page.goto(destination, wait_until="domcontentloaded", timeout=60000)
                await existing_page.bring_to_front()
                return {"ok": True, "message": "已打开该作业的独立窗口" if task_id else "已打开账号浏览器", "url": existing_page.url}
            old_context = self._contexts.pop(key, None)
            if old_context: await old_context.close()
            owned = self._workspaces.pop(key, None)
            if owned: owned[1].close()
            self._pages.pop(key, None)
            self._interactive_accounts.discard(key)
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            workspace = TaskBrowserProfile(account, "manual", task_id or account_id)
            context = None
            try:
                context = await self._playwright.chromium.launch_persistent_context(
                    str(workspace.path), headless=False, executable_path=str(chrome_executable()),
                    viewport={"width": 1360, "height": 900}, args=COURSE_BROWSER_ARGS + ["--start-maximized"],
                )
                await restore_state(context, account["storage_state_path"])
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(destination, wait_until="domcontentloaded", timeout=60000)
                self._contexts[key], self._pages[key] = context, page
                self._workspaces[key] = (context, workspace)
                self._interactive_accounts.add(key)
                context.on("close", lambda *_: asyncio.create_task(self._handle_context_close(key, context)))
                page.on("close", lambda *_: asyncio.create_task(self._handle_page_close(key, context, page)))
                return {"ok": True, "message": "已打开该作业的独立窗口" if task_id else "已打开账号浏览器", "url": page.url}
            except BaseException:
                try:
                    if context: await context.close()
                finally: workspace.close()
                raise

    async def _restore_cookies(self, context: BrowserContext, path: str) -> None:
        try:
            state = json.loads(open(path, encoding="utf-8").read())
        except (OSError, json.JSONDecodeError):
            return
        cookies = state.get("cookies") or []
        if cookies:
            await context.add_cookies(cookies)

    async def invalidate(self, account_id: str) -> None:
        async with self._lock:
            keys = [key for key in set(self._contexts) | set(self._workspaces)
                    if key == account_id or key.startswith(f"{account_id}:assignment:")]
            for key in keys:
                self._pages.pop(key, None)
                context = self._contexts.pop(key, None)
                if context: await context.close()
                owned = self._workspaces.pop(key, None)
                if owned: owned[1].close()
                self._interactive_accounts.discard(key)

    async def is_interactive(self, account_id: str) -> bool:
        async with self._lock:
            if account_id not in self._interactive_accounts:
                return False
            page = self._pages.get(account_id)
            context = self._contexts.get(account_id)
            if (
                page is None
                or page.is_closed()
                or context is None
            ):
                self._forget_locked(
                    account_id,
                    context=context,
                    page=page,
                )
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass
                return False
            return True

    async def close(self) -> None:
        for context in list(self._contexts.values()):
            try:
                await context.close()
            except Exception:
                pass
        self._contexts.clear()
        self._pages.clear()
        for _, workspace in self._workspaces.values(): workspace.close()
        self._workspaces.clear()
        self._interactive_accounts.clear()
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


preview_manager = ProfilePreviewManager()
