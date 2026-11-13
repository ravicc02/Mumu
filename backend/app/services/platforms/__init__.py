__all__ = ["platform_registry"]


def __getattr__(name):
    if name == "platform_registry":
        from .registry import platform_registry
        return platform_registry
    raise AttributeError(name)

__all__ = ["platform_registry"]
