from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


SETTING_KEY = "onboarding"
VERSION = 1


class OnboardingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["configured", "skipped"] = "skipped"
    platform: Literal["authorized", "skipped"] = "skipped"


def _choice(value: Any, allowed: set[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "skipped"


def load_onboarding(database: Any) -> dict[str, Any]:
    stored = database.get_setting(SETTING_KEY, {}) or {}
    completed = isinstance(stored, dict) and stored.get("completed") is True and stored.get("version") == VERSION
    return {
        "version": VERSION,
        "completed": completed,
        "completed_at": stored.get("completed_at") if completed else None,
        "model": _choice(stored.get("model"), {"configured", "skipped"}) if completed else "skipped",
        "platform": _choice(stored.get("platform"), {"authorized", "skipped"}) if completed else "skipped",
    }


def complete_onboarding(database: Any, update: OnboardingUpdate) -> dict[str, Any]:
    value = {
        "version": VERSION,
        "completed": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "model": update.model,
        "platform": update.platform,
    }
    database.set_setting(SETTING_KEY, value)
    return value
