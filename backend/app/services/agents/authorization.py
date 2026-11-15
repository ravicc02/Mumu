from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ...db import Database, utc_now


ChannelRole = Literal["viewer", "operator", "admin"]
ROLE_LEVEL = {"viewer": 1, "operator": 2, "admin": 3}


class ChannelUserRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: str
    external_user_id: str
    role: ChannelRole
    account_scope: list[str] = Field(default_factory=list)
    readonly_account_ids: list[str] = Field(default_factory=list)
    enabled: bool = False
    display_name: str | None = None
    created_at: str
    updated_at: str


class AuthorizationService:
    def __init__(self, database: Database):
        self.database = database

    def ensure_user(
        self,
        channel: str,
        external_user_id: str,
        *,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        channel, external_user_id = self._identity(channel, external_user_id)
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO channel_users(
                  channel, external_user_id, role, account_scope_json,
                  readonly_account_ids_json, enabled, display_name,
                  created_at, updated_at
                ) VALUES (?, ?, 'viewer', '[]', '[]', 0, ?, ?, ?)
                ON CONFLICT(channel, external_user_id) DO UPDATE SET
                  display_name = COALESCE(excluded.display_name, channel_users.display_name),
                  updated_at = channel_users.updated_at
                """,
                (
                    channel,
                    external_user_id,
                    str(display_name or "").strip()[:160] or None,
                    now,
                    now,
                ),
            )
        return self.get_user(channel, external_user_id)

    def set_user(
        self,
        channel: str,
        external_user_id: str,
        *,
        role: ChannelRole,
        account_scope: list[str],
        readonly_account_ids: list[str] | None = None,
        enabled: bool = True,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        channel, external_user_id = self._identity(channel, external_user_id)
        if role not in ROLE_LEVEL:
            raise ValueError("未知微信用户角色")
        scopes = _unique_strings(account_scope)
        if not scopes:
            raise ValueError("微信用户至少需要一个账号范围；全局范围请使用 *")
        if "*" in scopes and len(scopes) != 1:
            raise ValueError("全局账号范围不能与其他账号 ID 混用")
        readonly = _unique_strings(readonly_account_ids or [])
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO channel_users(
                  channel, external_user_id, role, account_scope_json,
                  readonly_account_ids_json, enabled, display_name,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, external_user_id) DO UPDATE SET
                  role = excluded.role,
                  account_scope_json = excluded.account_scope_json,
                  readonly_account_ids_json = excluded.readonly_account_ids_json,
                  enabled = excluded.enabled,
                  display_name = COALESCE(excluded.display_name, channel_users.display_name),
                  updated_at = excluded.updated_at
                """,
                (
                    channel,
                    external_user_id,
                    role,
                    json.dumps(scopes, ensure_ascii=False),
                    json.dumps(readonly, ensure_ascii=False),
                    int(bool(enabled)),
                    str(display_name or "").strip()[:160] or None,
                    now,
                    now,
                ),
            )
        return self.get_user(channel, external_user_id)

    def get_user(
        self,
        channel: str,
        external_user_id: str,
    ) -> dict[str, Any] | None:
        channel, external_user_id = self._identity(channel, external_user_id)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM channel_users
                WHERE channel = ? AND external_user_id = ?
                """,
                (channel, external_user_id),
            ).fetchone()
        return self._record(row) if row else None

    def list_users(self, channel: str | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if channel:
                rows = connection.execute(
                    "SELECT * FROM channel_users WHERE channel = ? ORDER BY role, created_at",
                    (str(channel).strip().lower(),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM channel_users ORDER BY channel, role, created_at"
                ).fetchall()
        return [self._record(row) for row in rows]

    def delete_user(self, channel: str, external_user_id: str) -> bool:
        channel, external_user_id = self._identity(channel, external_user_id)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM channel_users
                WHERE channel = ? AND external_user_id = ?
                """,
                (channel, external_user_id),
            )
        return bool(cursor.rowcount)

    def check(
        self,
        *,
        channel: str,
        external_user_id: str,
        action: Literal["read", "create", "control", "delete", "memory"],
        account_id: str | None = None,
    ) -> tuple[bool, str]:
        user = self.get_user(channel, external_user_id)
        if not user or not user["enabled"]:
            return False, "微信用户尚未获得授权"
        required_role = {
            "read": "viewer",
            "create": "operator",
            "control": "operator",
            "delete": "admin",
            "memory": "admin",
        }[action]
        if ROLE_LEVEL[user["role"]] < ROLE_LEVEL[required_role]:
            return False, "当前角色没有该操作权限"
        if account_id:
            if not self.account_in_scope(user, account_id):
                return False, "账号不在授权范围内"
            if action != "read" and account_id in user["readonly_account_ids"]:
                return False, "该账号已被设置为只读"
        return True, "ok"

    @staticmethod
    def account_in_scope(user: dict[str, Any], account_id: str) -> bool:
        scope = user.get("account_scope") or []
        return "*" in scope or str(account_id) in scope

    @staticmethod
    def permitted_account_ids(
        user: dict[str, Any],
        all_account_ids: list[str],
    ) -> list[str]:
        scope = user.get("account_scope") or []
        if "*" in scope:
            return list(all_account_ids)
        allowed = set(scope)
        return [identifier for identifier in all_account_ids if identifier in allowed]

    @staticmethod
    def mask_external_user_id(external_user_id: str) -> str:
        value = str(external_user_id)
        if len(value) <= 8:
            return value[:2] + "***"
        return f"{value[:4]}***{value[-4:]}"

    @staticmethod
    def _identity(channel: str, external_user_id: str) -> tuple[str, str]:
        channel_value = str(channel or "").strip().lower()
        user_value = str(external_user_id or "").strip()
        if not channel_value or len(channel_value) > 64:
            raise ValueError("微信通道标识无效")
        if not user_value or len(user_value) > 256:
            raise ValueError("微信用户 ID 无效")
        return channel_value, user_value

    @staticmethod
    def _record(row) -> dict[str, Any]:
        item = dict(row)
        item["account_scope"] = json.loads(item.pop("account_scope_json") or "[]")
        item["readonly_account_ids"] = json.loads(
            item.pop("readonly_account_ids_json") or "[]"
        )
        item["enabled"] = bool(item["enabled"])
        return ChannelUserRecord.model_validate(item).model_dump(mode="json")


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(
        str(value).strip()
        for value in values
        if str(value).strip()
    ))
