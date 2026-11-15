from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_KINDS = {"assignment", "learning", "manual"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_component(value: str) -> str:
    text = str(value or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,96}", text):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", text)[:48]
    return f"{cleaned or 'attempt'}-{digest}"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class TaskWorkspace:
    """Persistent workspace owned by one task attempt."""

    def __init__(
        self,
        data_root: Path,
        kind: str,
        attempt_id: str,
        *,
        account: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        task_spec: Any = None,
    ) -> None:
        if kind not in WORKSPACE_KINDS:
            raise ValueError("未知任务工作区类型")
        self.kind = kind
        self.attempt_id = str(attempt_id)
        self.data_root = data_root.resolve()
        self.root = (
            self.data_root
            / "task-workspaces"
            / kind
            / _safe_component(self.attempt_id)
        ).resolve()
        task_root = (self.data_root / "task-workspaces").resolve()
        if self.root.parent.parent != task_root:
            raise RuntimeError("任务工作区越过数据目录")
        self.checkpoints_dir = self.root / "checkpoints"
        self.input_dir = self.root / "input"
        self.scratch_dir = self.root / "scratch"
        self.evidence_dir = self.root / "evidence"
        self.browser_profile_path = self.root / "browser-profile"
        self.manifest_path = self.root / "manifest.json"
        for directory in (
            self.root,
            self.checkpoints_dir,
            self.input_dir,
            self.scratch_dir,
            self.evidence_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.manifest = self._load_or_create_manifest(account, metadata, task_spec)

    @classmethod
    def for_account(
        cls,
        account: dict[str, Any],
        kind: str,
        attempt_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        task_spec: Any = None,
    ) -> "TaskWorkspace":
        profile_dir = Path(account["profile_dir"]).resolve()
        data_root = profile_dir.parent.parent
        return cls(
            data_root,
            kind,
            attempt_id,
            account=account,
            metadata=metadata,
            task_spec=task_spec,
        )

    @staticmethod
    def _spec_data(task_spec: Any) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
        if task_spec is None:
            return None, None, None
        if hasattr(task_spec, "model_dump"):
            payload = task_spec.model_dump(mode="json")
        elif isinstance(task_spec, dict):
            payload = task_spec
        else:
            return None, None, None
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        permissions = payload.get("permissions")
        budget = payload.get("budget")
        return (
            hashlib.sha256(encoded).hexdigest(),
            permissions if isinstance(permissions, dict) else None,
            budget if isinstance(budget, dict) else None,
        )

    def _load_or_create_manifest(
        self,
        account: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
        task_spec: Any,
    ) -> dict[str, Any]:
        now = _utc_now()
        existing: dict[str, Any] = {}
        if self.manifest_path.is_file():
            try:
                parsed = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    existing = parsed
            except (OSError, ValueError):
                existing = {}
        allowed_metadata = {
            key: value
            for key, value in (metadata or {}).items()
            if key in {
                "route",
                "environment",
                "model_role",
                "platform",
                "trigger",
                "parent_attempt_id",
            }
        }
        spec_hash, permissions, budget = self._spec_data(task_spec)
        existing_account = (
            existing.get("account")
            if isinstance(existing.get("account"), dict)
            else {}
        )
        manifest = {
            "version": 1,
            "workspace_id": f"{self.kind}:{self.attempt_id}",
            "attempt_id": self.attempt_id,
            "attempt_kind": self.kind,
            "parent_attempt_id": (
                existing.get("parent_attempt_id")
                or allowed_metadata.get("parent_attempt_id")
            ),
            "task": {
                **(
                    existing.get("task")
                    if isinstance(existing.get("task"), dict)
                    else {}
                ),
                **allowed_metadata,
            },
            "account": {
                "id": account.get("id") if account else existing_account.get("id"),
                "platform": (
                    account.get("platform")
                    if account
                    else existing_account.get("platform")
                ),
            },
            "spec_hash": spec_hash or existing.get("spec_hash"),
            "permissions": permissions or existing.get("permissions"),
            "budget": budget or existing.get("budget"),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "heartbeat_at": now,
            "lease_keys": existing.get("lease_keys") or [],
            "retention_reason": existing.get("retention_reason"),
            "cleanup_at": existing.get("cleanup_at"),
            "inputs": existing.get("inputs") or [],
            "readonly_dependencies": existing.get("readonly_dependencies") or [],
            "artifacts": existing.get("artifacts") or [],
            "checkpoints": existing.get("checkpoints") or [],
        }
        _atomic_json(self.manifest_path, manifest)
        return manifest

    def update(
        self,
        *,
        phase: str | None = None,
        lease_keys: list[str] | None = None,
        retention_reason: str | None = None,
        cleanup_at: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        self.manifest["updated_at"] = now
        self.manifest["heartbeat_at"] = now
        if phase:
            self.manifest["phase"] = str(phase)
        if lease_keys is not None:
            self.manifest["lease_keys"] = list(dict.fromkeys(lease_keys))
        if retention_reason is not None:
            self.manifest["retention_reason"] = retention_reason
        if cleanup_at is not None:
            self.manifest["cleanup_at"] = cleanup_at
        _atomic_json(self.manifest_path, self.manifest)
        return dict(self.manifest)

    def set_task_spec(self, task_spec: Any) -> dict[str, Any]:
        spec_hash, permissions, budget = self._spec_data(task_spec)
        if not spec_hash:
            return dict(self.manifest)
        payload = (
            task_spec.model_dump(mode="json")
            if hasattr(task_spec, "model_dump")
            else dict(task_spec)
        )
        self.manifest["spec_hash"] = spec_hash
        self.manifest["permissions"] = permissions
        self.manifest["budget"] = budget
        task = self.manifest.get("task")
        if not isinstance(task, dict):
            task = {}
        task.update({
            key: payload[key]
            for key in (
                "route",
                "environment",
                "model_role",
                "instance_id",
                "challenge_id",
                "editable_files",
            )
            if key in payload
        })
        self.manifest["task"] = task
        self.manifest["updated_at"] = _utc_now()
        self.manifest["heartbeat_at"] = self.manifest["updated_at"]
        _atomic_json(self.manifest_path, self.manifest)
        return dict(self.manifest)

    def write_checkpoint(self, checkpoint: dict[str, Any]) -> Path:
        version = int(checkpoint["version"])
        source = {
            **checkpoint,
            "workspace_id": self.manifest["workspace_id"],
            "attempt_id": self.attempt_id,
        }
        path = self.checkpoints_dir / f"{version:06d}.json"
        _atomic_json(path, source)
        relative = path.relative_to(self.root).as_posix()
        entries = [
            item
            for item in self.manifest.get("checkpoints", [])
            if isinstance(item, dict) and item.get("version") != version
        ]
        entries.append({
            "version": version,
            "phase": source.get("phase"),
            "path": relative,
            "created_at": source.get("created_at"),
        })
        self.manifest["checkpoints"] = entries[-50:]
        self.manifest["updated_at"] = _utc_now()
        self.manifest["heartbeat_at"] = self.manifest["updated_at"]
        _atomic_json(self.manifest_path, self.manifest)
        return path

    def prepare_browser_profile(self) -> Path:
        target = self.browser_profile_path.resolve()
        if target.parent != self.root:
            raise RuntimeError("浏览器目录不在任务工作区内")
        deadline = time.monotonic() + 3
        while target.exists():
            try:
                shutil.rmtree(target)
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("旧任务浏览器目录仍被占用")
                time.sleep(0.1)
        target.mkdir(parents=True, exist_ok=False)
        return target

    @classmethod
    def purge_business_data_for_account(
        cls,
        account: dict[str, Any],
        kind: str,
        attempt_id: str,
    ) -> bool:
        if kind not in WORKSPACE_KINDS:
            raise ValueError("未知任务工作区类型")
        profile_dir = Path(account["profile_dir"]).resolve()
        data_root = profile_dir.parent.parent
        task_root = (data_root / "task-workspaces").resolve()
        root = (task_root / kind / _safe_component(attempt_id)).resolve()
        if root.parent.parent != task_root or not root.exists():
            return False
        for name in ("checkpoints", "input", "scratch"):
            target = (root / name).resolve()
            if target.parent != root:
                raise RuntimeError("任务业务目录越过工作区")
            if target.exists():
                shutil.rmtree(target)
        manifest = (root / "manifest.json").resolve()
        if manifest.parent != root:
            raise RuntimeError("任务清单越过工作区")
        try:
            manifest.unlink()
        except FileNotFoundError:
            pass
        return True
