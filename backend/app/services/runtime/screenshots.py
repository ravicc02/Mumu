from __future__ import annotations

import base64
import inspect
import math
from typing import Any, AsyncIterator


def screenshot_regions(width: float, height: float, page_height: int = 1000, overlap: int = 64) -> list[dict[str, int]]:
    width, height = math.ceil(width), math.ceil(height)
    if width <= 0 or height <= 0 or page_height <= overlap or overlap < 0:
        raise ValueError("截图页面尺寸或分段参数无效")
    regions = []
    y = 0
    while y < height:
        current_height = min(page_height, height - y)
        regions.append({"x": 0, "y": y, "width": width, "height": current_height})
        if y + current_height >= height:
            break
        y += page_height - overlap
    return regions


async def capture_screenshot_segments(session: Any) -> AsyncIterator[dict[str, Any]]:
    page = await session.get_current_page()
    if page is None:
        raise RuntimeError("没有可截图的浏览器页面")
    session_id = page.session_id
    if inspect.isawaitable(session_id):
        session_id = await session_id
    session_id = str(session_id)
    client = session.cdp_client.send.Page
    metrics = await client.getLayoutMetrics(session_id=session_id)
    content = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
    viewport = metrics.get("cssLayoutViewport") or metrics.get("layoutViewport") or {}
    width = min(content.get("width", 0), viewport.get("clientWidth") or content.get("width", 0))
    page_height = max(1, int(viewport.get("clientHeight") or 900))
    regions = screenshot_regions(width, content.get("height", 0), page_height, overlap=min(64, page_height // 10))
    url = await session.get_current_page_url()
    for index, region in enumerate(regions, 1):
        result = await client.captureScreenshot({
            "format": "png", "fromSurface": True, "captureBeyondViewport": True,
            "clip": {**region, "scale": 1},
        }, session_id=session_id)
        yield {**region, "index": index, "count": len(regions), "image": base64.b64decode(result["data"]), "url": url}
