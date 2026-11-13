from __future__ import annotations

from math import ceil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PreferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssignmentPreferences(PreferenceModel):
    auto_submit: bool = False
    educoder_auto_evaluate: bool = True
    interactive: bool = True
    max_steps: int = Field(default=100, ge=10, le=500)
    concurrency: int = Field(default=1, ge=1, le=8)

    @model_validator(mode="after")
    def enable_task_browser(self):
        self.interactive = True
        return self


class LearningPreferences(PreferenceModel):
    headless: bool = True
    playback_rate: float = Field(default=1, ge=0.5, le=2)
    volume: int = Field(default=0, ge=0, le=100)
    resource_wait_seconds: int = Field(default=15, ge=5, le=120)
    confirmation_wait_seconds: int = Field(default=25, ge=10, le=180)
    stall_seconds: int = Field(default=60, ge=30, le=300)


class SystemPreferences(PreferenceModel):
    collection_concurrency: int = Field(default=2, ge=1, le=8)
    log_retention_days: int = Field(default=14, ge=1, le=365)
    log_size_mb: int = Field(default=200, ge=20, le=4096)


class Preferences(PreferenceModel):
    assignments: AssignmentPreferences = Field(default_factory=AssignmentPreferences)
    learning: LearningPreferences = Field(default_factory=LearningPreferences)
    system: SystemPreferences = Field(default_factory=SystemPreferences)


class AccountSchedule(PreferenceModel):
    account_id: str
    enabled: bool
    auto_execute: bool = False
    interval_value: float = Field(ge=1, le=604800)
    interval_unit: Literal["s", "min", "h"]

    @property
    def seconds(self) -> int:
        return ceil(self.interval_value * {"s": 1, "min": 60, "h": 3600}[self.interval_unit])

    @model_validator(mode="after")
    def validate_interval(self):
        actual = self.interval_value * {"s": 1, "min": 60, "h": 3600}[self.interval_unit]
        if not 5 <= actual <= 604800:
            raise ValueError("检测间隔须为 5 秒至 7 天")
        if self.auto_execute and not self.enabled:
            raise ValueError("自动完成作业需要启用自动检测")
        return self


class PreferencesUpdate(PreferenceModel):
    assignments: AssignmentPreferences | None = None
    learning: LearningPreferences | None = None
    system: SystemPreferences | None = None
    schedules: list[AccountSchedule] | None = None


def load_preferences(database) -> Preferences:
    return Preferences.model_validate(database.get_setting("preferences", {}) or {})


def collection_interval_seconds(account) -> int:
    return max(5, int(account.get("auto_collect_interval_seconds") or
                      int(account.get("auto_collect_interval_minutes") or 360) * 60))
