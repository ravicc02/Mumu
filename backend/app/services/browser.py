from __future__ import annotations

import os
import shutil
import logging
import re
import tempfile
from pathlib import Path


COURSE_BROWSER_ARGS = [
    "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding", "--disable-features=CalculateNativeWinOcclusion",
]


class TaskBrowserProfile:
    """Own a task's Chrome data while reading authorization from its account."""

    def __init__(self, account: dict, kind: str, identifier: str = ""):
        if kind not in {"assignment", "learning", "manual"}:
            raise ValueError("Unknown browser task kind")
        canonical = Path(account["profile_dir"]).resolve()
        self.root = canonical.parent / "task-browsers" / canonical.name
        self.root.mkdir(parents=True, exist_ok=True)
        label = re.sub(r"[^a-zA-Z0-9_-]", "", identifier)[:64]
        self.path = Path(tempfile.mkdtemp(prefix=f"{kind}-{label}-", dir=self.root)).resolve()
        self.closed = False

    def close(self):
        if self.closed:
            return
        root, target = self.root.resolve(), self.path.resolve()
        if target.parent != root or not target.is_relative_to(root):
            raise RuntimeError("Refusing to remove a browser directory outside its task root")
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            pass
        except OSError:
            logging.getLogger("answer-studio.browser").warning("Task Chrome directory remains locked: %s", target.name)
            return
        self.closed = True


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
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("未找到 Chrome 或 Edge，请先安装 Chromium 浏览器")
