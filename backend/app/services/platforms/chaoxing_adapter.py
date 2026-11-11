from __future__ import annotations

from typing import Any, Awaitable, Callable

from ...db import Database
from .. import chaoxing
from .base import PlatformAdapter


class ChaoxingAdapter(PlatformAdapter):
    name = "chaoxing"
    label = "学习通"

    def start_login(
        self,
        database: Database,
        account_id: str,
        settings: Any,
    ) -> dict[str, Any]:
        return chaoxing.start_login(database, account_id, settings)

    def login_status(self, account_id: str) -> dict[str, Any]:
        return chaoxing.login_status(account_id)

    async def collect_assignments(
        self,
        database: Database,
        account_id: str,
        academic_year: int,
        run_id: str | None = None,
        progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        return await chaoxing.collect_assignments(
            database,
            account_id,
            academic_year,
            run_id=run_id,
            progress=progress,
        )

    async def refresh_session(
        self,
        database: Database,
        account_id: str,
    ) -> dict[str, Any]:
        return await chaoxing.refresh_session(database, account_id)
