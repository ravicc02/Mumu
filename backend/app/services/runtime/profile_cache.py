"""清理 Chrome 用户数据目录中的可再生缓存。

只删除缓存、浏览历史、崩溃采样等可再生内容，保留登录态与账号资料
（Default/Network/Cookies、Local Storage、IndexedDB、Preferences、
Login Data、Web Data、storage-state.json、avatar.bin）。

安全约束：只处理白名单目录与文件，不做模糊匹配；目录不存在或删除失败
时直接跳过，不影响任务执行。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("mumu.browser.cache")

TOP_LEVEL_DIRECTORIES = (
    "GrShaderCache",
    "ShaderCache",
    "GraphiteDawnCache",
    "Crashpad",
    "BrowserMetrics",
    "component_crx_cache",
    "extensions_crx_cache",
    "optimization_guide_model_store",
    "segmentation_platform",
)
TOP_LEVEL_FILES = ("BrowserMetrics-spare.pma", "CrashpadMetrics-active.pma")
DEFAULT_DIRECTORIES = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "History",
    "History-journal",
    "Favicons",
    "Sessions",
    "Sessions_Encrypted",
    "JumpListIconsMostVisited",
    "JumpListIconsRecentClosed",
    "Site Characteristics Database",
    "shared_proto_db",
)

# 必须保留的登录态与账号资料，用于自检与文档。
PROTECTED_PATHS = (
    "Default/Network/Cookies",
    "Default/Local Storage",
    "Default/IndexedDB",
    "Default/Preferences",
    "Default/Login Data",
    "Default/Web Data",
    "storage-state.json",
    "avatar.bin",
)


def _path_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for current, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(current, name))
            except OSError:
                continue
    return total


def _remove(path: Path) -> int:
    """删除文件或目录，返回实际释放的字节数。"""
    if not path.exists():
        return 0
    size = _path_size(path)
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        return 0
    return 0 if path.exists() else size


def prune_browser_cache(profile_dir: Path) -> dict[str, int]:
    """清理一个 Chrome 用户数据目录中的可再生缓存。"""
    profile = Path(profile_dir)
    if not profile.is_dir():
        return {"freed": 0}
    freed = 0
    for name in TOP_LEVEL_DIRECTORIES:
        freed += _remove(profile / name)
    for name in TOP_LEVEL_FILES:
        freed += _remove(profile / name)
    default = profile / "Default"
    if default.is_dir():
        for name in DEFAULT_DIRECTORIES:
            freed += _remove(default / name)
    return {"freed": freed}


def prune_accounts(accounts_root: Path) -> dict[str, int]:
    """清理数据根下所有账号的浏览器缓存。"""
    root = Path(accounts_root)
    accounts = 0
    freed = 0
    if not root.is_dir():
        return {"accounts": 0, "freed": 0}
    for account_dir in root.iterdir():
        browser = account_dir / "browser"
        if not browser.is_dir():
            continue
        result = prune_browser_cache(browser)
        if result["freed"]:
            accounts += 1
            freed += result["freed"]
    if freed:
        logger.info(
            "浏览器缓存已清理",
            extra={
                "diagnostic_code": "mumu.browser.cache_pruned",
                "diagnostic_data": {"accounts": accounts, "freed_mb": round(freed / 1048576, 1)},
            },
        )
    return {"accounts": accounts, "freed": freed}
