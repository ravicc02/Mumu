from __future__ import annotations

import ctypes
import logging
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .product import DATA_DIRECTORY, DATABASE_FILENAME, LEGACY_DATA_DIRECTORY


PROJECT_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("mumu.config")


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
    explicit = (
        os.getenv("MUMU_DATA", "").strip()
        or os.getenv("ANSWER_STUDIO_DATA", "").strip()
    )
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path.home() / DATA_DIRECTORY).resolve()


def legacy_data_root() -> Path | None:
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    return (Path(local_app_data) / LEGACY_DATA_DIRECTORY).resolve()


def _tree_stats(root: Path) -> tuple[int, int]:
    count = 0
    size = 0
    for path in root.rglob("*"):
        count += 1
        if path.is_file() and not path.is_symlink():
            size += path.stat().st_size
    return count, size


def _long_path(path: Path) -> Path:
    resolved = path.resolve()
    if os.name != "nt":
        return resolved
    value = str(resolved)
    if value.startswith("\\\\?\\"):
        return Path(value)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


def _windows_path_alias(path: Path, function: str) -> Path | None:
    if os.name != "nt":
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    length = getattr(ctypes.windll.kernel32, function)(str(path), buffer, len(buffer))
    return Path(buffer.value) if 0 < length < len(buffer) else None


def _path_aliases(path: Path) -> tuple[Path, ...]:
    candidates = [path, path.resolve(), path.absolute()]
    if os.name == "nt":
        for candidate in tuple(candidates):
            for function in ("GetLongPathNameW", "GetShortPathNameW"):
                alias = _windows_path_alias(candidate, function)
                if alias is not None:
                    candidates.append(alias)
    unique = []
    seen = set()
    for candidate in candidates:
        identity = os.path.normcase(os.path.abspath(str(candidate)))
        if identity not in seen:
            seen.add(identity)
            unique.append(candidate)
    return tuple(unique)


def _canonical_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if os.name == "nt":
        direct = _windows_path_alias(path, "GetLongPathNameW")
        if direct is not None:
            return direct
        parent = _windows_path_alias(path.parent, "GetLongPathNameW")
        if parent is not None:
            return parent / path.name
    return path.resolve()


def _rebind_database_paths(
    database: Path,
    source: Path,
    target: Path,
    *,
    source_aliases: tuple[Path, ...] | None = None,
) -> int:
    if not database.is_file():
        return 0
    replacements = []
    for source_path in source_aliases or _path_aliases(source):
        for old, new in (
            (str(source_path), str(target)),
            (str(source_path).replace("\\", "\\\\"), str(target).replace("\\", "\\\\")),
            (str(source_path).replace("\\", "/"), str(target).replace("\\", "/")),
        ):
            if (old, new) not in replacements:
                replacements.append((old, new))
    updated = 0
    connection = sqlite3.connect(str(_long_path(database)))
    try:
        with connection:
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                quoted_table = '"' + table.replace('"', '""') + '"'
                columns = connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
                for column in columns:
                    name = str(column[1])
                    declared_type = str(column[2] or "").upper()
                    if any(token in declared_type for token in ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC", "BOOL")):
                        continue
                    quoted_column = '"' + name.replace('"', '""') + '"'
                    expression = quoted_column
                    conditions = []
                    expression_parameters = []
                    condition_parameters = []
                    for old, new in replacements:
                        expression = f"REPLACE({expression}, ?, ?)"
                        conditions.append(f"INSTR({quoted_column}, ?) > 0")
                        expression_parameters.extend((old, new))
                        condition_parameters.append(old)
                    statement = (
                        f"UPDATE {quoted_table} SET {quoted_column} = {expression} "
                        f"WHERE {' OR '.join(conditions)}"
                    )
                    result = connection.execute(
                        statement,
                        expression_parameters + condition_parameters,
                    )
                    updated += max(0, result.rowcount)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    return updated


def migrate_legacy_data_root(target: Path, *, source: Path | None = None) -> Path:
    """Copy legacy user data into the new home directory without overwriting it."""
    if (
        os.getenv("MUMU_DATA", "").strip()
        or os.getenv("ANSWER_STUDIO_DATA", "").strip()
    ):
        return target
    requested_source = source or legacy_data_root()
    if requested_source is None:
        return target
    source_aliases = _path_aliases(requested_source)
    source = _canonical_path(requested_source)
    target = target.expanduser().absolute()
    if target.exists():
        target = _canonical_path(target)
    if source == target or not source.is_dir():
        return target
    if target.exists() and any(target.iterdir()):
        updated = _rebind_database_paths(
            target / DATABASE_FILENAME,
            source,
            target,
            source_aliases=source_aliases,
        )
        if updated:
            logger.info(
                "Legacy data paths rebound",
                extra={
                    "diagnostic_code": "mumu.data_root.paths_rebound",
                    "diagnostic_data": {"rows": updated, "target": str(target)},
                },
            )
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    target = _canonical_path(target)
    staging = target.parent / f"{target.name}.migrating-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        shutil.copytree(_long_path(source), _long_path(staging), symlinks=True)
        if _tree_stats(_long_path(source)) != _tree_stats(_long_path(staging)):
            raise OSError("迁移后的文件数量或大小与旧目录不一致")
        updated = _rebind_database_paths(
            staging / DATABASE_FILENAME,
            source,
            target,
            source_aliases=source_aliases,
        )
        if target.exists():
            target.rmdir()
        staging.replace(target)
        logger.info(
            "Legacy data root migrated",
            extra={
                "diagnostic_code": "mumu.data_root.migrated",
                "diagnostic_data": {
                    "source": str(source),
                    "target": str(target),
                    "rows_rebound": updated,
                },
            },
        )
        return target
    except Exception as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError(
            f"无法把旧数据目录迁移到 {target}。旧数据仍保留在 {source}，请检查磁盘空间或权限后重试"
        ) from error


@dataclass(frozen=True)
class Settings:
    data_root: Path = field(default_factory=default_data_root)
    host: str = os.getenv("MUMU_HOST") or os.getenv("ANSWER_STUDIO_HOST", "127.0.0.1")
    port: int = int(os.getenv("MUMU_PORT") or os.getenv("ANSWER_STUDIO_PORT", "8877"))
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    openai_fallback_model: str = os.getenv("OPENAI_FALLBACK_MODEL", "")
    reasoning_effort: str = os.getenv("AI_REASONING_EFFORT", "high")
    agent_max_steps: int = 100
    webhook_url: str = os.getenv("MUMU_WEBHOOK_URL") or os.getenv("ANSWER_STUDIO_WEBHOOK_URL", "")
    weixin_bot_token: str = os.getenv("WEIXIN_BOT_TOKEN", "")
    weixin_user_id: str = os.getenv("WEIXIN_USER_ID", "")
    weixin_context_token: str = os.getenv("WEIXIN_CONTEXT_TOKEN", "")
    weixin_base_url: str = os.getenv(
        "WEIXIN_BASE_URL",
        "https://ilinkai.weixin.qq.com",
    )
    attendance_share_base_url: str = os.getenv("MUMU_ATTENDANCE_SHARE_URL", "")
    attendance_share_admin_secret: str = os.getenv("MUMU_ATTENDANCE_SHARE_SECRET", "")

    @property
    def database_path(self) -> Path:
        return self.data_root / DATABASE_FILENAME

    @property
    def profiles_root(self) -> Path:
        return self.data_root / "profiles"

    @property
    def artifacts_root(self) -> Path:
        return self.data_root / "artifacts"

    @property
    def task_workspaces_root(self) -> Path:
        return self.data_root / "task-workspaces"

    @property
    def secrets_root(self) -> Path:
        return self.data_root / "secrets"

    @property
    def logs_root(self) -> Path:
        return self.data_root / "logs"

    def ensure_directories(self) -> None:
        for path in (
            self.data_root,
            self.profiles_root,
            self.artifacts_root,
            self.task_workspaces_root,
            self.secrets_root,
            self.logs_root,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                path.chmod(0o700)


settings = Settings()
