from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ...db import Database


class PlatformAdapter(ABC):
    name: str
    label: str

    @abstractmethod
    def start_login(
        self,
        database: Database,
        account_id: str,
        settings: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def login_status(self, account_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def collect_assignments(
        self,
        database: Database,
        account_id: str,
        academic_year: int,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def refresh_session(
        self,
        database: Database,
        account_id: str,
    ) -> dict[str, Any]:
        raise NotImplementedError
