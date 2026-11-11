from __future__ import annotations

import os
import shutil
from pathlib import Path


def chrome_executable() -> Path:
    candidates = [
        os.getenv("ANSWER_STUDIO_CHROME_PATH", ""),
        shutil.which("chrome") or "",
        shutil.which("chrome.exe") or "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
        ),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("未找到 Chrome 或 Edge，请先安装 Chromium 浏览器")
