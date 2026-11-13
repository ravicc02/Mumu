"""Resolve the repository version or the copy bundled with the backend."""
import re
from pathlib import Path


def read_version() -> str:
    candidates = (Path(__file__).with_name("VERSION"), Path(__file__).resolve().parents[2] / "VERSION")
    for path in candidates:
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?", value)
            if not match or (match[4] and any(part.isdigit() and len(part) > 1 and part[0] == '0' for part in match[4].split('.'))):
                raise RuntimeError("Invalid application VERSION")
            return value
    raise RuntimeError("Application VERSION is missing from the source or backend bundle")


__version__ = read_version()
