from __future__ import annotations

from .base import PlatformAdapter
from .chaoxing_adapter import ChaoxingAdapter


class PlatformRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> PlatformAdapter:
        try:
            return self._adapters[name]
        except KeyError as error:
            raise ValueError(f"暂不支持平台：{name}") from error

    def list(self) -> list[dict[str, str]]:
        return [
            {"name": adapter.name, "label": adapter.label}
            for adapter in self._adapters.values()
        ]


platform_registry = PlatformRegistry()
platform_registry.register(ChaoxingAdapter())
