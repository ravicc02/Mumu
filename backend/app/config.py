from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(PROJECT_ROOT / ".env")


def default_data_root() -> Path:
    explicit = os.getenv("ANSWER_STUDIO_DATA", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "AIAssistantStudio"
    return PROJECT_ROOT / "app-data"


@dataclass(frozen=True)
class Settings:
    data_root: Path = default_data_root()
    host: str = os.getenv("ANSWER_STUDIO_HOST", "127.0.0.1")
    port: int = int(os.getenv("ANSWER_STUDIO_PORT", "8877"))
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    openai_fallback_model: str = os.getenv("OPENAI_FALLBACK_MODEL", "")
    reasoning_effort: str = os.getenv("AI_REASONING_EFFORT", "high")
    agent_max_steps: int = 100
    webhook_url: str = os.getenv("ANSWER_STUDIO_WEBHOOK_URL", "")
    weixin_bot_token: str = os.getenv("WEIXIN_BOT_TOKEN", "")
    weixin_user_id: str = os.getenv("WEIXIN_USER_ID", "")
    weixin_context_token: str = os.getenv("WEIXIN_CONTEXT_TOKEN", "")
    weixin_base_url: str = os.getenv(
        "WEIXIN_BASE_URL",
        "https://ilinkai.weixin.qq.com",
    )

    @property
    def database_path(self) -> Path:
        return self.data_root / "answer-studio.sqlite3"

    @property
    def profiles_root(self) -> Path:
        return self.data_root / "profiles"

    @property
    def artifacts_root(self) -> Path:
        return self.data_root / "artifacts"

    @property
    def logs_root(self) -> Path:
        return self.data_root / "logs"

    def ensure_directories(self) -> None:
        for path in (
            self.data_root,
            self.profiles_root,
            self.artifacts_root,
            self.logs_root,
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
