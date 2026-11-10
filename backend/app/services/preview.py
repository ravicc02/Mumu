from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from ..db import Database


class ProfilePreviewManager:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}
        self._interactive_accounts: set[str] = set()
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        if self._browser and self._browser.is_connected():
            return self._browser
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
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
                context = await browser.new_context(
                    storage_state=storage_state,
                    viewport={"width": 1280, "height": 820},
                    device_scale_factor=1,
                )
                self._contexts[account_id] = context
            page = self._pages.get(account_id)
            if page is None or page.is_closed():
                page = await context.new_page()
                self._pages[account_id] = page
                reload = True
            if reload or page.url == "about:blank":
                await page.goto(
                    "https://i.chaoxing.com/base?ws=1",
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
        self,
        database: Database,
        account_id: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            account = database.get_account(account_id)
            destination = target_url or "https://i.chaoxing.com/base?ws=1"
            existing_page = self._pages.get(account_id)
            existing_context = self._contexts.get(account_id)
            if (
                account_id in self._interactive_accounts
                and existing_context is not None
                and existing_page is not None
                and not existing_page.is_closed()
            ):
                if existing_page.url != destination:
                    await existing_page.goto(
                        destination,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    await existing_page.wait_for_timeout(500)
                await existing_page.bring_to_front()
                return {
                    "ok": True,
                    "message": "已在当前账号浏览器中打开",
                    "url": existing_page.url,
                }
            old_page = self._pages.pop(account_id, None)
            if old_page and not old_page.is_closed():
                await old_page.close()
            old_context = self._contexts.pop(account_id, None)
            if old_context:
                await old_context.close()
            self._interactive_accounts.discard(account_id)
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            context = await self._playwright.chromium.launch_persistent_context(
                account["profile_dir"],
                headless=False,
                viewport={"width": 1360, "height": 900},
                args=["--start-maximized"],
            )
            await self._restore_cookies(context, account["storage_state_path"])
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(
                destination,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await page.wait_for_timeout(800)
            self._contexts[account_id] = context
            self._pages[account_id] = page
            self._interactive_accounts.add(account_id)
            return {
                "ok": True,
                "message": (
                    "已在当前账号浏览器中打开"
                    if target_url
                    else "可操控浏览器已打开"
                ),
                "url": page.url,
            }

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
            page = self._pages.pop(account_id, None)
            if page and not page.is_closed():
                await page.close()
            context = self._contexts.pop(account_id, None)
            if context:
                await context.close()
            self._interactive_accounts.discard(account_id)

    async def close(self) -> None:
        for context in list(self._contexts.values()):
            try:
                await context.close()
            except Exception:
                pass
        self._contexts.clear()
        self._pages.clear()
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


preview_manager = ProfilePreviewManager()
