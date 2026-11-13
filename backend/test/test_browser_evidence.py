from __future__ import annotations

import struct
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright

from app.services.browser import chrome_executable
from app.services.draft_saver import DraftSaveError, save_assignment_draft
from app.services.screenshot_capture import capture_screenshot_segments, screenshot_regions


class ScreenshotRegionTests(unittest.TestCase):
    def test_long_page_has_bounded_segments_overlap_and_no_missing_tail(self):
        regions = screenshot_regions(1440, 5237, 900)
        self.assertEqual(regions[0]["y"], 0)
        for left, right in zip(regions, regions[1:]):
            self.assertEqual(left["y"] + left["height"] - right["y"], 64)
        self.assertTrue(all(region["height"] <= 900 for region in regions))
        self.assertEqual(regions[-1]["y"] + regions[-1]["height"], 5237)

    def test_short_page_and_invalid_geometry(self):
        self.assertEqual(len(screenshot_regions(1440, 500)), 1)
        for width, height in [(0, 100), (100, 0), (-1, 100)]:
            with self.assertRaises(ValueError): screenshot_regions(width, height)


class DraftSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_requires_positive_platform_feedback(self):
        for result in ({"saved": False, "reason": "保存失败"}, {}, "invalid"):
            session = SimpleNamespace(get_current_page=AsyncMock(return_value=SimpleNamespace(evaluate=AsyncMock(return_value=result))))
            with self.assertRaises(DraftSaveError): await save_assignment_draft(session)


class BrowserEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_chrome_clicks_only_draft_save_and_reports_server_failure(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                await page.set_content('''<input id="answer" value="draft text"><button onclick="window.submits++">提交</button>
                    <a onclick="saveWork()">暂时保存</a><script>
                    window.submits=0; window.clicks=0;
                    window.saveWork=function(options) { window.clicks++; setTimeout(() => {
                        window.persisted=document.querySelector('#answer').value;
                        options.success({status:true});
                    }, 10); };</script>''')
                session = SimpleNamespace(get_current_page=AsyncMock(return_value=page))
                result = await save_assignment_draft(session)
                self.assertEqual(result["evidence"], "platform_callback")
                self.assertEqual(await page.evaluate("[window.submits, window.clicks, window.persisted]"), [0, 1, "draft text"])
                await page.evaluate("() => { window.saveWork = options => options.failure({status:false,msg:'保存失败：网络异常'}); }")
                with self.assertRaisesRegex(DraftSaveError, "网络异常"):
                    await save_assignment_draft(session)
                await page.set_content('<button onclick="window.submitted=true">提交</button>')
                with self.assertRaisesRegex(DraftSaveError, "未找到"):
                    await save_assignment_draft(session)
                self.assertIsNone(await page.evaluate("window.submitted"))
            finally:
                await browser.close()

    async def test_real_chrome_captures_readable_tiles_without_moving_the_page(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                await page.set_content('<style>body{margin:0} section{height:600px;background:#f5f5f5;border-bottom:1px solid #333}</style>' + ''.join(f'<section>Question {i}</section>' for i in range(9)))
                await page.evaluate("window.scrollTo(0, 1200)")
                cdp = await page.context.new_cdp_session(page)
                calls = []
                async def capture(params, session_id):
                    calls.append(params)
                    return await cdp.send("Page.captureScreenshot", params)
                async def metrics(session_id): return await cdp.send("Page.getLayoutMetrics")
                session = SimpleNamespace(
                    get_current_page=AsyncMock(return_value=SimpleNamespace(session_id="test")),
                    get_current_page_url=AsyncMock(return_value="http://fixture.test/work"),
                    cdp_client=SimpleNamespace(send=SimpleNamespace(Page=SimpleNamespace(captureScreenshot=capture, getLayoutMetrics=metrics))),
                )
                sections = [item async for item in capture_screenshot_segments(session)]
                self.assertGreater(len(sections), 5)
                for section in sections:
                    width, height = struct.unpack(">II", section["image"][16:24])
                    self.assertEqual(width, 1280)
                    self.assertLessEqual(height, 800)
                self.assertTrue(all("clip" in call for call in calls))
                self.assertEqual(await page.evaluate("window.scrollY"), 1200)
                content_height = (await cdp.send("Page.getLayoutMetrics"))["cssContentSize"]["height"]
                self.assertEqual(sections[-1]["y"] + sections[-1]["height"], content_height)
            finally:
                await browser.close()
