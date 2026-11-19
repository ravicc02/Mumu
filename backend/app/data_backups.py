from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from .version import __version__


class DataBackupRequest(BaseModel):
    include_accounts: bool = True
    include_artifacts: bool = False
    include_browser_profile: bool = False


class BackupPolicy(BaseModel):
    frequency: str = Field(default="off", pattern="^(off|daily|weekly)$")
    keep: int = Field(default=3, ge=1, le=10)


def archive_name(name: str) -> str:
    parts = PurePosixPath(name).parts
    if not parts or name != "/".join(parts) or any(
        p in {".", ".."} or ":" in p or "\\" in p or p.endswith((".", " "))
        or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in parts
    ):
        raise ValueError("备份包含无效路径")
    if name == "database/mumu.sqlite3":
        return name
    if len(parts) >= 3 and parts[0] == "accounts" and re.fullmatch(r"[a-zA-Z0-9_-]+", parts[1]):
        if len(parts) == 3 and parts[2] in {"account.json", "storage-state.json"}:
            return name
        if len(parts) >= 4 and parts[2] in {"browser", "artifacts"}:
            return name
    raise ValueError("备份包含不允许的内容")


def safe_files(root: Path):
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        return
    for current, directories, files in os.walk(root):
        directories[:] = [n for n in directories if not (Path(current) / n).is_symlink() and not (Path(current) / n).is_junction()]
        for name in files:
            source = Path(current) / name
            if source.is_file() and not source.is_symlink():
                yield source


def backup_root(root: Path) -> Path:
    target = root / "backups"
    if target.is_symlink() or target.is_junction():
        raise ValueError("备份目录不能是链接")
    target.mkdir(parents=True, exist_ok=True)
    return target


def create_backup(root: Path, db, request: DataBackupRequest, *, automatic=False, snapshot=False) -> dict:
    root = root.resolve()
    destination = backup_root(root)
    identifier = f"{'before-restore' if snapshot else 'auto' if automatic else 'backup'}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.zip"
    target = destination / identifier
    pending = target.with_suffix(".partial")
    manifest = {"schema_version": 1, "app_version": __version__, "data_root": str(root),
                "created_at": datetime.now(timezone.utc).isoformat(), "automatic": automatic,
                "contents": {"database": True, "accounts": request.include_accounts,
                             "artifacts": request.include_artifacts, "browser_profile": request.include_browser_profile}, "files": []}
    try:
        with tempfile.TemporaryDirectory(dir=destination) as temporary:
            copied_db = Path(temporary) / "mumu.sqlite3"
            with db.connect() as source, closing(sqlite3.connect(copied_db)) as output:
                source.backup(output)
            entries = [("database/mumu.sqlite3", copied_db)]
            for account in db.list_accounts():
                identifier_account = str(account["id"])
                account_root = root / "accounts" / identifier_account
                if account_root.is_symlink() or account_root.is_junction():
                    raise ValueError("账号目录不能是链接")
                if request.include_accounts:
                    state = Path(account["storage_state_path"])
                    if state.is_file() and not state.is_symlink() and state.resolve().is_relative_to(account_root.resolve()):
                        entries.append((state.relative_to(root).as_posix(), state))
                for include, folder in ((request.include_artifacts, "artifacts"), (request.include_browser_profile, "browser")):
                    if include:
                        entries.extend((p.relative_to(root).as_posix(), p) for p in safe_files(account_root / folder) or ())
            with zipfile.ZipFile(pending, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, source in entries:
                    archive_name(name)
                    digest = hashlib.sha256()
                    size = 0
                    with source.open("rb") as input_file, archive.open(name, "w", force_zip64=True) as output_file:
                        while chunk := input_file.read(1024 * 1024):
                            digest.update(chunk)
                            size += len(chunk)
                            output_file.write(chunk)
                    manifest["files"].append({"path": name, "bytes": size, "sha256": digest.hexdigest()})
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            os.replace(pending, target)
        return {"id": target.name, "bytes": target.stat().st_size, "created_at": manifest["created_at"], "contents": manifest["contents"], "valid": True}
    finally:
        pending.unlink(missing_ok=True)


def verify_backup(path: Path, directory: Path) -> dict:
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("备份路径无效")
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(n.casefold() for n in names)) or len(names) > 100000:
                raise ValueError("备份包含重复文件或文件过多")
            if sum(info.file_size for info in infos) > 20 * 1024**3:
                raise ValueError("备份解压体积超过 20 GiB 限制")
            if archive.getinfo("manifest.json").file_size > 16 * 1024**2:
                raise ValueError("备份清单过大")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("schema_version") != 1 or manifest.get("app_version") != __version__:
                raise ValueError("备份版本与当前应用不匹配")
            declared = []
            for item in manifest["files"]:
                name = archive_name(item["path"])
                declared.append(name)
                if archive.getinfo(name).file_size != item["bytes"]:
                    raise ValueError("备份文件大小不匹配")
                digest = hashlib.sha256()
                with archive.open(name) as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != item["sha256"]:
                    raise ValueError("备份文件校验失败")
            if len(declared) != len(set(declared)) or set(names) != set(declared) | {"manifest.json"} or "database/mumu.sqlite3" not in declared:
                raise ValueError("备份清单与实际文件不一致")
        return {"valid": True, "manifest": manifest}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, RuntimeError) as error:
        return {"valid": False, "error": str(error)}


def restore_backup(root: Path, db, backup_id: str) -> dict:
    directory = backup_root(root)
    if Path(backup_id).name != backup_id or not backup_id.endswith(".zip"):
        raise ValueError("备份标识无效")
    path = directory / backup_id
    checked = verify_backup(path, directory)
    if not checked["valid"]:
        raise ValueError(checked["error"])
    manifest = checked["manifest"]
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(path) as archive:
            for item in manifest["files"]:
                name = archive_name(item["path"])
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        staged_db = staging / "database/mumu.sqlite3"
        with closing(sqlite3.connect(staged_db)) as connection, db.connect() as current:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("备份数据库完整性检查失败")
            schema = lambda c: c.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            if [(r[0], r[1]) for r in schema(connection)] != [(r[0], r[1]) for r in schema(current)]:
                raise ValueError("备份数据库表结构与当前数据不匹配")
            old_root = manifest.get("data_root", "")
            if old_root and old_root != str(root):
                raise ValueError("当前仅支持恢复到原数据目录，不支持跨机器或跨目录迁移")
            connection.commit()
        targets = []
        for item in manifest["files"]:
            if item["path"].startswith("accounts/"):
                target = root / item["path"]
                parent = target
                while parent != root:
                    if parent.is_symlink() or parent.is_junction():
                        raise ValueError("恢复目标不能经过链接")
                    parent = parent.parent
                targets.append((staging / item["path"], target))
        snapshot = create_backup(root, db, DataBackupRequest(include_artifacts=True, include_browser_profile=True), snapshot=True)
        rollback = staging / "rollback"
        saved = []
        try:
            for index, (source, target) in enumerate(targets):
                previous = rollback / str(index)
                existed = target.exists()
                if existed:
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, previous)
                saved.append((target, previous, existed))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            with closing(sqlite3.connect(staged_db)) as source, db.connect() as output:
                source.backup(output)
        except Exception:
            for target, previous, existed in reversed(saved):
                shutil.copy2(previous, target) if existed else target.unlink(missing_ok=True)
            with zipfile.ZipFile(directory / snapshot["id"]) as archive:
                rollback_db = staging / "rollback.sqlite3"
                rollback_db.write_bytes(archive.read("database/mumu.sqlite3"))
            with closing(sqlite3.connect(rollback_db)) as source, db.connect() as output:
                source.backup(output)
            raise
    return {"backup_id": backup_id, "snapshot": snapshot, "restored": len(manifest["files"])}


def automatic_backup(root: Path, db, *, now=None) -> dict | None:
    policy = BackupPolicy.model_validate(db.get_setting("data_backup_policy", {}) or {})
    if policy.frequency == "off":
        return None
    now = now or datetime.now(timezone.utc)
    last = db.get_setting("data_backup_last_auto", None)
    if last and now - datetime.fromisoformat(last) < timedelta(days=1 if policy.frequency == "daily" else 7):
        return None
    result = create_backup(root, db, DataBackupRequest(), automatic=True)
    db.set_setting("data_backup_last_auto", now.isoformat())
    candidates = sorted(backup_root(root).glob("auto-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for candidate in candidates[policy.keep:]:
        if not candidate.is_symlink() and verify_backup(candidate, candidate.parent)["valid"]:
            candidate.unlink()
    return result
