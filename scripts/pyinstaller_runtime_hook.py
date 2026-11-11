from __future__ import annotations

import platform
import sys


if sys.platform == "win32":
    platform.system = lambda: "Windows"
    platform.platform = lambda: "Windows"
    platform.machine = lambda: "AMD64"
    platform.processor = lambda: "AMD64"
    platform.release = lambda: "10"
    platform.version = lambda: "10.0"
    platform.win32_ver = lambda: (
        "10",
        "10.0",
        "SP0",
        "Multiprocessor Free",
    )
