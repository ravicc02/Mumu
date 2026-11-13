import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.screenshot_capture import capture_screenshot_segments


class CaptureViewportTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_segments_use_actual_viewport_including_heights_above_1000(self):
        capture = AsyncMock(return_value={"data": base64.b64encode(b"image").decode()})
        session = SimpleNamespace(
            get_current_page=AsyncMock(return_value=SimpleNamespace(session_id="page")),
            get_current_page_url=AsyncMock(return_value="http://fixture.test/work"),
            cdp_client=SimpleNamespace(send=SimpleNamespace(Page=SimpleNamespace(
                getLayoutMetrics=AsyncMock(return_value={"cssContentSize": {"width": 1440, "height": 3150}, "cssLayoutViewport": {"clientWidth": 1440, "clientHeight": 1200}}),
                captureScreenshot=capture))),
        )
        sections = [part async for part in capture_screenshot_segments(session)]
        self.assertEqual([part["height"] for part in sections], [1200, 1200, 878])
        self.assertEqual(sections[-1]["y"] + sections[-1]["height"], 3150)
        self.assertTrue(all(part["width"] == 1440 for part in sections))
