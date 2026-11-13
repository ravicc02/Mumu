from __future__ import annotations

import argparse
import logging

import uvicorn

from app.config import settings
from app.diagnostics.logging import configure_logging

settings.ensure_directories()
configure_logging(settings)
try:
    from app.main import app
except Exception:
    logging.getLogger("mumu.startup").exception("Backend initialization failed")
    raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8877)
    args = parser.parse_args()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        log_config=None,
    )


if __name__ == "__main__":
    main()
