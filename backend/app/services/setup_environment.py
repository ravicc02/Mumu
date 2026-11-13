from __future__ import annotations

import os

from .browser import chrome_executable


def setup_environment(settings):
    items = [{"id": "runtime", "label": "本地运行环境", "status": "ready", "detail": "本地服务已启动", "installable": False}]
    browser = {"id": "browser", "label": "Chrome / Edge", "installable": False,
        "command": "winget install --id Google.Chrome --exact --accept-package-agreements --accept-source-agreements"}
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
