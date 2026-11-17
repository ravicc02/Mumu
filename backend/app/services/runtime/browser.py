from __future__ import annotations

import os
import shutil
import logging
import time
from pathlib import Path

from ..agents.workspace import TaskWorkspace


COURSE_BROWSER_ARGS = [
    "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding", "--disable-features=CalculateNativeWinOcclusion",
]


class TaskBrowserProfile:
    """Own a task's Chrome data while reading authorization from its account."""

    def __init__(
        self,
        account: dict,
        kind: str,
        identifier: str = "",
        *,
        metadata: dict | None = None,
        task_spec=None,
        workspace: TaskWorkspace | None = None,
    ):
        if kind not in {"assignment", "learning", "manual"}:
            raise ValueError("Unknown browser task kind")
        self.workspace = workspace or TaskWorkspace.for_account(
            account,
            kind,
            identifier,
            metadata=metadata,
            task_spec=task_spec,
        )
        if self.workspace.kind != kind or self.workspace.attempt_id != str(identifier):
            raise ValueError("任务工作区与浏览器尝试不一致")
        self.root = self.workspace.root
        self.path = self.workspace.prepare_browser_profile()
        self.closed = False

    def close(self):
        if self.closed:
            return
        expected = self.workspace.browser_profile_path.resolve()
        root, target = self.root.resolve(), self.path.resolve()
        if target != expected or target.parent != root:
            raise RuntimeError("Refusing to remove a browser directory outside its task workspace")
        deadline = time.monotonic() + 3
        while True:
            try:
                shutil.rmtree(target)
            except FileNotFoundError:
                pass
            except OSError:
                if time.monotonic() < deadline:
                    time.sleep(0.1)
                    continue
                logging.getLogger("mumu.browser").warning("Task Chrome directory remains locked: %s", target.name)
                return
            self.closed = True
            return


def chrome_executable() -> Path:
    candidates = [
        os.getenv("MUMU_CHROME_PATH", "") or os.getenv("ANSWER_STUDIO_CHROME_PATH", ""),
        shutil.which("chrome") or "",
        shutil.which("chrome.exe") or "",
        *[shutil.which(name) or "" for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "microsoft-edge-stable")],
        "/snap/bin/chromium",
        "/opt/google/chrome/chrome",
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
