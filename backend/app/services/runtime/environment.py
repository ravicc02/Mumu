from __future__ import annotations

import os
import sys

from .browser import chrome_executable


def setup_environment(settings):
    items = [{"id": "runtime", "label": "本地运行环境", "status": "ready", "detail": "本地服务已启动", "installable": False}]
    linux = sys.platform.startswith("linux")
    browser = {"id": "browser", "label": "Chrome / Edge / Chromium", "installable": False,
        "command": "安装 Google Chrome、Microsoft Edge 或发行版提供的 Chromium；也可用 MUMU_CHROME_PATH 指定浏览器路径。" if linux else "winget install --id Google.Chrome --exact --accept-package-agreements --accept-source-agreements"}
    try:
        browser.update(status="ready", detail=str(chrome_executable()))
    except RuntimeError as error:
        browser.update(status="missing", detail=str(error))
    items.append(browser)
    writable = settings.data_root.is_dir() and os.access(settings.data_root, os.W_OK)
    items.append({"id": "storage", "label": "本地数据目录", "status": "ready" if writable else "error",
        "detail": str(settings.data_root), "installable": False})
    return {"status": "ready" if all(item["status"] == "ready" for item in items) else "blocked",
        "backendReady": True, "scope": str(settings.data_root), "items": items, "error": "", "output": ""}
