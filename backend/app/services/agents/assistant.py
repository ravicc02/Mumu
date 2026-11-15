from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from ...db import utc_now
from ...preferences import load_preferences
from ..assignments.readiness import execution_block
from ..learning.store import ACTIVE as LEARNING_ACTIVE, LearningStore
from .authorization import AuthorizationService
from .confirmation import ConfirmationService
from .eventing import IncidentCenter, OutboxService
from .memory import MemoryService
from .tools import ToolLedger


WRITE_WORDS = ("创建", "开启", "开始", "执行", "暂停", "继续", "停止", "重试", "删除", "删掉")


class MumuAssistant:
    def __init__(
        self,
        database_provider: Callable[[], Any],
        settings_provider: Callable[[], Any],
        *,
        learning_service,
        orchestrator,
        general_responder: Callable[[dict[str, Any]], Awaitable[str]] | None = None,
    ) -> None:
        self.database_provider = database_provider
        self.settings_provider = settings_provider
        self.learning_service = learning_service
        self.orchestrator = orchestrator
        self.general_responder = general_responder
        self._session_locks: dict[str, asyncio.Lock] = {}

    @property
    def database(self):
        return self.database_provider()

    @property
    def settings(self):
        return self.settings_provider()

    def _memory(self) -> MemoryService:
        return MemoryService(self.database, self.settings)

    def _auth(self) -> AuthorizationService:
        return AuthorizationService(self.database)

    def _confirmation(self) -> ConfirmationService:
        return ConfirmationService(self.database, self.settings)

    def _outbox(self) -> OutboxService:
        return OutboxService(self.database, self.settings)

    def _incidents(self) -> IncidentCenter:
        outbox = self._outbox()
        return IncidentCenter(self.database, outbox, self.settings)

    def _tools(self) -> ToolLedger:
        return ToolLedger(self.database, self.settings)

    async def handle_message(
        self,
        *,
        channel: str,
        external_user_id: str,
        text: str,
        inbound_message_id: str,
        message_type: str = "text",
    ) -> dict[str, Any]:
        channel = str(channel).strip().lower()
        external_user_id = str(external_user_id).strip()
        cleaned_text = self._memory().scrubber.text(text).strip()
        if not cleaned_text:
            return {"status": "ignored", "reason": "empty_message"}
        auth = self._auth()
        user = auth.ensure_user(channel, external_user_id)
        if not user["enabled"]:
            reply = "这个微信用户尚未获得 Mumu 管理授权。请在桌面端设置角色和账号范围。"
            await self._enqueue_unbound_reply(
                channel,
                external_user_id,
                inbound_message_id,
                reply,
            )
            return {"status": "unauthorized", "queued": True}
        recent = self._recent_message_count(channel, external_user_id)
        if recent > 20:
            reply = "请求过于频繁，请稍后再试。"
            await self._enqueue_unbound_reply(
                channel,
                external_user_id,
                inbound_message_id,
                reply,
            )
            return {"status": "rate_limited", "queued": True}

        session = self._memory().find_active_session(
            agent_kind="assistant",
            channel=channel,
            external_user_ref=external_user_id,
        )
        if session is None:
            session = self._memory().create_session(
                agent_kind="assistant",
                channel=channel,
                external_user_ref=external_user_id,
                title="Mumu Assistant",
            )
        session_id = session["id"]
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            self._memory().append_message(
                session_id=session_id,
                role="user",
                content={"text": cleaned_text, "message_type": message_type},
                kind="message",
            )
            try:
                result = await self._handle_authorized(
                    channel=channel,
                    external_user_id=external_user_id,
                    session_id=session_id,
                    inbound_message_id=inbound_message_id,
                    text=cleaned_text,
                    user=user,
                )
            except Exception as error:
                self._memory().append_message(
                    session_id=session_id,
                    role="tool",
                    content={"tool": "assistant", "error": type(error).__name__},
                    kind="tool_result",
                )
                result = {
                    "status": "error",
                    "reply": "处理请求时发生错误。请检查桌面端日志后重试。",
                }
            reply = str(result.get("reply") or "").strip()
            reply_session_id = str(result.get("new_session_id") or session_id)
            if reply:
                self._memory().append_message(
                    session_id=reply_session_id,
                    role="assistant",
                    content={"text": reply},
                    kind="message",
                )
                outbox_item = self._outbox().enqueue(
                    channel=channel,
                    external_user_id=external_user_id,
                    dedupe_key=(
                        f"assistant-reply:{reply_session_id}:{inbound_message_id}:"
                        f"{hashlib.sha256(reply.encode('utf-8')).hexdigest()[:16]}"
                    ),
                    payload={"kind": "assistant_reply", "text": reply},
                )
                result["outbox_id"] = outbox_item["id"]
            result.update({
                "session_id": reply_session_id,
                "queued": bool(reply),
            })
            return result

    async def _handle_authorized(
        self,
        *,
        channel: str,
        external_user_id: str,
        session_id: str,
        inbound_message_id: str,
        text: str,
        user: dict[str, Any],
        forced_account_id: str | None = None,
    ) -> dict[str, Any]:
        command = text.strip()
        if command == "/whoami":
            scope = "全局" if "*" in user["account_scope"] else "、".join(
                item[-8:] for item in user["account_scope"]
            )
            return {
                "status": "ok",
                "reply": (
                    f"微信用户：{AuthorizationService.mask_external_user_id(external_user_id)}\n"
                    f"角色：{user['role']}\n账号范围：{scope or '无'}"
                ),
                "tool": "authorization.whoami",
            }
        if command in {"/new", "/reset"}:
            self._memory().archive_active_sessions(
                agent_kind="assistant",
                channel=channel,
                external_user_ref=external_user_id,
            )
            new_session = self._memory().create_session(
                agent_kind="assistant",
                channel=channel,
                external_user_ref=external_user_id,
                title="Mumu Assistant",
            )
            return {
                "status": "ok",
                "reply": "已开始新的对话上下文。任务记录没有改变。",
                "new_session_id": new_session["id"],
                "tool": "session.new",
            }
        if command == "/stop":
            return {
                "status": "ok",
                "reply": "当前没有正在生成的回复。`/stop` 不会停止业务任务。",
                "tool": "assistant.stop",
            }
        if command.startswith("/confirm"):
            token = command.removeprefix("/confirm").strip()
            return await self._confirm_action(
                channel=channel,
                external_user_id=external_user_id,
                session_id=session_id,
                token=token,
                user=user,
            )
        if command.startswith("/cancel"):
            token = command.removeprefix("/cancel").strip()
            cancelled = self._confirmation().cancel(
                channel=channel,
                external_user_id=external_user_id,
                session_id=session_id,
                token=token,
            )
            return {
                "status": "ok" if cancelled else "not_found",
                "reply": "动作已取消。" if cancelled else "没有找到可取消的待确认动作。",
                "tool": "confirmation.cancel",
            }
        if _looks_like_confirmation(command):
            token = _confirmation_token(command)
            return await self._confirm_action(
                channel=channel,
                external_user_id=external_user_id,
                session_id=session_id,
                token=token,
                user=user,
            )
        if _is_batch_write(command):
            return {
                "status": "blocked",
                "reply": "首期不开放批量写操作，请一次指定一个账号和一个任务。",
                "tool": "assistant.batch_guard",
            }

        selection_result = await self._handle_selection(
            channel=channel,
            external_user_id=external_user_id,
            session_id=session_id,
            inbound_message_id=inbound_message_id,
            text=text,
            user=user,
        )
        if selection_result is not None:
            return selection_result

        allowed, reason = self._auth().check(
            channel=channel,
            external_user_id=external_user_id,
            action="read",
        )
        if not allowed:
            return {"status": "forbidden", "reply": reason}

        lower = command.lower()
        if any(word in command for word in ("总况", "汇报", "概况", "整体情况")):
            return await self._overview_tool(
                channel,
                external_user_id,
                inbound_message_id,
                user,
            )
        if any(word in command for word in ("哪个任务错", "异常", "失败任务", "中断")):
            return await self._errors_tool(
                channel,
                external_user_id,
                inbound_message_id,
                user,
            )
        if "多少" in command and "作业" in command and any(
            word in command for word in ("没做完", "未完成", "剩下")
        ):
            return await self._remaining_assignment_tool(
                channel,
                external_user_id,
                session_id,
                inbound_message_id,
                user,
                command,
                forced_account_id=forced_account_id,
            )
        if any(word in command for word in ("刷课", "网课")) and any(
            word in command for word in ("开启", "创建", "开始")
        ):
            return await self._prepare_learning_action(
                channel,
                external_user_id,
                session_id,
                inbound_message_id,
                user,
                command,
                forced_account_id=forced_account_id,
            )
        if (
            "作业" in command
            and any(
                word in command
                for word in ("做了", "帮我做", "执行", "创建", "开始做", "完成")
            )
            and not any(
                word in command
                for word in (
                    "做好了吗",
                    "做完了吗",
                    "完成了吗",
                    "完成了没",
                    "是否完成",
                    "有没有完成",
                    "进度",
                    "多少",
                    "怎么样了",
                )
            )
        ):
            return await self._prepare_assignment_action(
                channel,
                external_user_id,
                session_id,
                inbound_message_id,
                user,
                command,
                forced_account_id=forced_account_id,
            )
        if any(word in command for word in ("删除", "删掉")):
            return await self._prepare_task_action(
                "delete",
                channel,
                external_user_id,
                session_id,
                inbound_message_id,
                user,
                command,
            )
        for action, words in (
            ("pause", ("暂停",)),
            ("resume", ("继续",)),
            ("stop", ("停止", "结束")),
            ("retry", ("重试",)),
        ):
            if any(word in command for word in words) and "任务" in command:
                return await self._prepare_task_action(
                    action,
                    channel,
                    external_user_id,
                    session_id,
                    inbound_message_id,
                    user,
                    command,
                )
        if any(
            word in command
            for word in (
                "进度",
                "做好了吗",
                "做完了吗",
                "完成了吗",
                "完成了没",
                "完成情况",
                "怎么样了",
                "到哪",
            )
        ):
            return await self._task_progress_tool(
                channel,
                external_user_id,
                session_id,
                inbound_message_id,
                user,
                command,
            )
        return await self._general_answer(
            channel=channel,
            external_user_id=external_user_id,
            session_id=session_id,
            user=user,
            text=text,
        )

    async def _overview_tool(self, channel, external_user_id, inbound_message_id, user):
        async def operation():
            accounts = self._permitted_accounts(user)
            account_ids = {item["id"] for item in accounts}
            jobs = [
                job for job in self.database.list_jobs(include_deleted=False)
                if job["account_id"] in account_ids
            ]
            store = LearningStore(self.database)
            runs = [
                run for run in store.runs()
                if run["account_id"] in account_ids
            ]
            queued = sum(job["status"] == "queued" for job in jobs) + sum(
                run["status"] == "queued" for run in runs
            )
            running = sum(job["status"] == "running" for job in jobs) + sum(
                run["status"] == "running" for run in runs
            )
            intervention = sum(job["status"] == "needs_intervention" for job in jobs)
            abnormal = sum(
                job["status"] in {"interrupted", "incomplete", "failed"}
                for job in jobs
            ) + sum(
                run["status"] in {"interrupted", "incomplete"}
                for run in runs
            )
            waiting_upstream = sum(
                (job.get("phase") == "waiting_upstream")
                for job in jobs
            ) + sum(
                (run.get("phase") == "waiting_upstream")
                for run in runs
            )
            incidents = self._incidents().list(status="open", limit=20)
            unhealthy = [
                row for row in self._provider_health()
                if row["state"] != "closed"
            ]
            return {
                "accounts": len(accounts),
                "authorization_errors": sum(
                    account["status"] != "ready" for account in accounts
                ),
                "queued": queued,
                "running": running,
                "needs_intervention": intervention,
                "abnormal": abnormal,
                "waiting_upstream": waiting_upstream,
                "incidents": len(incidents),
                "unhealthy_upstreams": len(unhealthy),
            }

        result = await self._run_tool(
            "platform.overview",
            {},
            operation,
            channel=channel,
            external_user_id=external_user_id,
            idempotency_key=f"overview:{inbound_message_id}",
        )
        reply = (
            f"账号 {result['accounts']} 个，授权异常 {result['authorization_errors']} 个\n"
            f"运行中 {result['running']}，排队 {result['queued']}，"
            f"待介入 {result['needs_intervention']}，异常 {result['abnormal']}\n"
            f"等待上游 {result['waiting_upstream']}，未关闭事故 {result['incidents']}，"
            f"异常上游 {result['unhealthy_upstreams']}"
        )
        return {"status": "ok", "reply": reply, "tool": "platform.overview", "data": result}

    async def _errors_tool(self, channel, external_user_id, inbound_message_id, user):
        async def operation():
            account_ids = {item["id"] for item in self._permitted_accounts(user)}
            items = []
            for job in self.database.list_jobs(include_deleted=False):
                if job["account_id"] not in account_ids:
                    continue
                if job["status"] in {"interrupted", "incomplete", "failed"} or job.get(
                    "submission_status"
                ) in {"submission_unknown", "submitted_unverified"}:
                    items.append({
                        "kind": "assignment",
                        "id": job["id"],
                        "title": job.get("assignment_title"),
                        "course": job.get("course_name"),
                        "status": job["status"],
                        "submission_status": job.get("submission_status"),
                        "error": job.get("error") or "",
                    })
            store = LearningStore(self.database)
            for run in store.runs():
                if run["account_id"] in account_ids and run["status"] in {
                    "interrupted", "incomplete"
                }:
                    items.append({
                        "kind": "learning",
                        "id": run["id"],
                        "title": run.get("course_name"),
                        "status": run["status"],
                        "error": run.get("error") or "",
                    })
            return items[:10]

        items = await self._run_tool(
            "tasks.list",
            {"status": "interrupted,incomplete,unknown"},
            operation,
            channel=channel,
            external_user_id=external_user_id,
            idempotency_key=f"errors:{inbound_message_id}",
        )
        if not items:
            return {"status": "ok", "reply": "当前没有需要处理的异常任务。", "tool": "tasks.list"}
        lines = ["需要处理的任务："]
        for index, item in enumerate(items, start=1):
            reason = _status_reason(item["status"], item.get("submission_status"))
            lines.append(
                f"{index}. {item.get('title') or item.get('course')} "
                f"[{item['kind']} {item['id'][:8]}]：{reason}"
            )
        return {
            "status": "ok",
            "reply": "\n".join(lines),
            "tool": "tasks.list",
            "data": items,
        }

    async def _remaining_assignment_tool(
        self,
        channel,
        external_user_id,
        session_id,
        inbound_message_id,
        user,
        text,
        *,
        forced_account_id=None,
    ):
        accounts = self._permitted_accounts(user)
        if not accounts:
            return {
                "status": "forbidden",
                "reply": "当前微信用户没有可用账号范围。",
            }
        account, candidates = self._resolve_account(
            text,
            user,
            accounts,
            forced_account_id=forced_account_id,
        )
        if candidates:
            return self._selection_reply(
                session_id,
                "account",
                candidates,
                text,
                inbound_message_id,
                purpose="assignment_count",
            )

        async def operation():
            year = _academic_year()
            assignments = self.database.list_assignments(
                account_id=account["id"],
                academic_year=year,
                submittable_only=False,
            )
            incomplete = [
                item for item in assignments
                if item.get("active")
                and item.get("submittable")
                and str(item.get("status", "")).lower() in {
                    "unfinished", "未交", "未完成",
                }
            ]
            latest = max(
                (item.get("updated_at") for item in assignments if item.get("updated_at")),
                default="",
            )
            return {
                "account": account["name"],
                "count": len(incomplete),
                "year": year,
                "updated_at": latest or "未知",
                "collection_error": account.get("last_collection_error") or "",
            }

        result = await self._run_tool(
            "assignments.count",
            {"account_id": account["id"], "academic_year": _academic_year()},
            operation,
            channel=channel,
            external_user_id=external_user_id,
            idempotency_key=f"assignment-count:{inbound_message_id}",
        )
        warning = (
            f"\n注意：最近采集不完整，{result['collection_error'][:120]}"
            if result["collection_error"]
            else ""
        )
        return {
            "status": "ok",
            "reply": (
                f"{result['account']} 当前学年可提交未完成作业 {result['count']} 个。\n"
                f"数据更新时间：{result['updated_at']}{warning}"
            ),
            "tool": "assignments.count",
            "data": result,
        }

    async def _prepare_learning_action(
        self,
        channel,
        external_user_id,
        session_id,
        inbound_message_id,
        user,
        text,
        *,
        forced_account_id=None,
    ):
        allowed, reason = self._auth().check(
            channel=channel,
            external_user_id=external_user_id,
            action="create",
        )
        if not allowed:
            return {"status": "forbidden", "reply": reason}
        accounts = self._permitted_accounts(user)
        if not accounts:
            return {
                "status": "forbidden",
                "reply": "当前微信用户没有可用账号范围。",
            }
        account, candidates = self._resolve_account(
            text,
            user,
            accounts,
            forced_account_id=forced_account_id,
        )
        if candidates:
            return self._selection_reply(
                session_id,
                "account",
                candidates,
                text,
                inbound_message_id,
                purpose="create_learning",
            )
        allowed, reason = self._auth().check(
            channel=channel,
            external_user_id=external_user_id,
            action="create",
            account_id=account["id"],
        )
        if not allowed:
            return {"status": "forbidden", "reply": reason}
        courses = LearningStore(self.database).courses(account["id"], _academic_year())
        if not courses:
            return {
                "status": "not_found",
                "reply": "当前账号在本学年没有已采集的可用课程。",
            }
        course, course_candidates = self._resolve_named_entity(text, courses, "name")
        if course_candidates:
            self._memory().update_session_metadata(
                session_id,
                {"selection": {
                    "kind": "course",
                    "account_id": account["id"],
                    "candidates": [
                        {"id": item["id"], "label": item["name"]}
                        for item in course_candidates
                    ],
                    "original_text": text,
                    "inbound_message_id": inbound_message_id,
                    "purpose": "create_learning",
                }},
            )
            return _candidate_reply("课程", course_candidates, "name")
        blocked = self._learning_preflight(account, course)
        if blocked:
            return {"status": "blocked", "reply": blocked}
        payload = {
            "kind": "create_learning",
            "account_id": account["id"],
            "course_id": course["id"],
        }
        preview = (
            "准备创建网课任务\n"
            f"账号：{account['name']} · {_platform_label(account['platform'])} · "
            f"尾号 {account['id'][-4:]}\n"
            f"课程：{course['name']}\n"
            "模式：后台任务，沿用当前网课设置；不修改账号授权。\n"
        )
        return self._issue_write_confirmation(
            channel,
            external_user_id,
            session_id,
            inbound_message_id,
            payload,
            preview,
        )

    async def _prepare_assignment_action(
        self,
        channel,
        external_user_id,
        session_id,
        inbound_message_id,
        user,
        text,
        *,
        forced_account_id=None,
    ):
        allowed, reason = self._auth().check(
            channel=channel,
            external_user_id=external_user_id,
            action="create",
        )
        if not allowed:
            return {"status": "forbidden", "reply": reason}
        accounts = self._permitted_accounts(user)
        if not accounts:
            return {
                "status": "forbidden",
                "reply": "当前微信用户没有可用账号范围。",
            }
        account, candidates = self._resolve_account(
            text,
            user,
            accounts,
            forced_account_id=forced_account_id,
        )
        if candidates:
            return self._selection_reply(
                session_id,
                "account",
                candidates,
                text,
                inbound_message_id,
                purpose="create_assignment",
            )
        allowed, reason = self._auth().check(
            channel=channel,
            external_user_id=external_user_id,
            action="create",
            account_id=account["id"],
        )
        if not allowed:
            return {"status": "forbidden", "reply": reason}
        assignments = self.database.list_assignments(
            account_id=account["id"],
            academic_year=_academic_year(),
            submittable_only=True,
        )
        if not assignments:
            return {
                "status": "not_found",
                "reply": "当前账号在本学年没有可提交作业。",
            }
        assignment, assignment_candidates = self._resolve_named_entity(
            text,
            assignments,
            "title",
        )
        if assignment_candidates:
            self._memory().update_session_metadata(
                session_id,
                {"selection": {
                    "kind": "assignment",
                    "account_id": account["id"],
                    "candidates": [
                        {
                            "id": item["id"],
                            "label": f"{item['course_name']} / {item['title']}",
                        }
                        for item in assignment_candidates
                    ],
                    "original_text": text,
                    "inbound_message_id": inbound_message_id,
                    "purpose": "create_assignment",
                }},
            )
            return _candidate_reply("作业", assignment_candidates, "title")
        blocked = self._assignment_preflight(account, assignment)
        if blocked:
            return {"status": "blocked", "reply": blocked}
        payload = {
            "kind": "create_assignment",
            "account_id": account["id"],
            "assignment_id": assignment["id"],
            "auto_submit": False,
            "auto_evaluate": False,
        }
        preview = (
            "准备创建作业任务\n"
            f"账号：{account['name']} · {_platform_label(account['platform'])} · "
            f"尾号 {account['id'][-4:]}\n"
            f"作业：{assignment['course_name']} / {assignment['title']}\n"
            f"截止：{assignment.get('deadline_text') or '未提供'}\n"
            "模式：填写并暂存，不最终提交，不开启代码评测。\n"
        )
        return self._issue_write_confirmation(
            channel,
            external_user_id,
            session_id,
            inbound_message_id,
            payload,
            preview,
        )

    async def _prepare_task_action(
        self,
        action,
        channel,
        external_user_id,
        session_id,
        inbound_message_id,
        user,
        text,
    ):
        permission = "delete" if action == "delete" else "control"
        allowed, reason = self._auth().check(
            channel=channel,
            external_user_id=external_user_id,
            action=permission,
        )
        if not allowed:
            return {"status": "forbidden", "reply": reason}
        tasks = self._permitted_tasks(user)
        if not tasks:
            return {
                "status": "not_found",
                "reply": "当前授权账号范围内没有可操作任务。",
            }
        task, candidates = self._resolve_task_from_text(text, tasks)
        if candidates:
            return self._selection_reply(
                session_id,
                "task",
                candidates,
                text,
                inbound_message_id,
                purpose="control",
                extra={"action": action},
            )
        allowed, reason = self._auth().check(
            channel=channel,
            external_user_id=external_user_id,
            action=permission,
            account_id=task["account_id"],
        )
        if not allowed:
            return {"status": "forbidden", "reply": reason}
        payload = {
            "kind": "task_control" if action != "delete" else "task_delete",
            "action": action,
            "task_kind": task["kind"],
            "task_id": task["id"],
            "account_id": task["account_id"],
        }
        preview = (
            f"准备{_action_label(action)}任务\n"
            f"账号：{task.get('account_name') or task['account_id'][-8:]}\n"
            f"任务：{task.get('title') or task.get('course_name')} "
            f"[{task['kind']} {task['id'][:8]}]\n"
            f"当前状态：{task['status']}\n"
            + (
                "影响：仅隐藏本地任务记录及历史尝试，不操作平台数据。\n"
                if action == "delete"
                else "影响：复用现有任务状态机，确认后会再次校验。\n"
            )
        )
        return self._issue_write_confirmation(
            channel,
            external_user_id,
            session_id,
            inbound_message_id,
            payload,
            preview,
        )

    async def _task_progress_tool(
        self,
        channel,
        external_user_id,
        session_id,
        inbound_message_id,
        user,
        text,
        *,
        forced_task_id=None,
    ):
        tasks = self._permitted_tasks(user)
        if not tasks:
            return {
                "status": "not_found",
                "reply": "当前授权账号范围内没有可操作任务。",
                "tool": "tasks.resolve",
            }
        if forced_task_id:
            task = next(
                (item for item in tasks if item["id"] == forced_task_id),
                None,
            )
            candidates = []
            if task is None:
                return {
                    "status": "forbidden",
                    "reply": "该任务不在当前微信用户的账号范围内。",
                }
        else:
            task, candidates = self._resolve_task_from_text(text, tasks)
        if candidates:
            return self._selection_reply(
                session_id,
                "task",
                candidates,
                text,
                inbound_message_id,
                purpose="progress",
            )
        checkpoint = self.database.latest_task_checkpoint(task["id"])
        reply = _task_status_text(task, checkpoint)
        return {
            "status": "ok",
            "reply": reply,
            "tool": "tasks.get",
            "data": {"task": task, "checkpoint": checkpoint},
        }

    async def _confirm_action(
        self,
        *,
        channel,
        external_user_id,
        session_id,
        token,
        user,
    ):
        result = self._confirmation().consume(
            channel=channel,
            external_user_id=external_user_id,
            session_id=session_id,
            token=token,
        )
        if result.status == "already_consumed":
            return {
                "status": "duplicate",
                "reply": "该确认码已经使用，Mumu 没有重复执行。",
                "tool": "confirmation.consume",
            }
        if result.status != "confirmed" or not result.payload:
            return {
                "status": result.status,
                "reply": {
                    "invalid": "确认码无效。",
                    "expired": "确认码已过期，请重新发起操作。",
                    "cancelled": "该动作已取消。",
                }.get(result.status, "确认码不可用。"),
                "tool": "confirmation.consume",
            }
        payload = result.payload
        action = "delete" if payload["kind"] == "task_delete" else (
            "control" if payload["kind"] == "task_control" else "create"
        )
        allowed, reason = self._auth().check(
            channel=channel,
            external_user_id=external_user_id,
            action=action,
            account_id=payload.get("account_id"),
        )
        if not allowed:
            return {"status": "forbidden", "reply": reason}

        async def operation():
            return await self._execute_action(payload, user, external_user_id)

        try:
            execution = await self._run_tool(
                f"tasks.{action}",
                payload,
                operation,
                attempt_id=payload.get("task_id"),
                channel=channel,
                external_user_id=external_user_id,
                idempotency_key=(
                    f"confirmed:{result.consumed_at}:{payload['kind']}"
                ),
            )
        except Exception as error:
            detail = self._memory().scrubber.text(str(error)).strip()
            return {
                "status": "failed",
                "reply": (
                    f"操作未执行：{detail or '业务校验未通过。'}\n"
                    "该确认码已使用，请重新发起操作。"
                ),
                "tool": f"tasks.{action}",
            }
        return {
            "status": "ok",
            "reply": execution["reply"],
            "tool": f"tasks.{action}",
            "data": execution,
        }

    async def _execute_action(self, payload, user, external_user_id):
        kind = payload["kind"]
        if kind == "create_learning":
            course = LearningStore(self.database).course(payload["course_id"])
            if course["account_id"] != payload.get("account_id"):
                raise RuntimeError("课程与账号不匹配，请重新发起操作。")
            account = self.database.get_account(course["account_id"])
            blocked = self._learning_preflight(account, course)
            if blocked:
                raise RuntimeError(blocked)
            run = self.learning_service.start(course["id"])
            return {
                "reply": f"网课任务已进入队列：{course['name']} [{run['id'][:8]}]",
                "task_kind": "learning",
                "task_id": run["id"],
            }
        if kind == "create_assignment":
            assignment = self.database.get_assignment(payload["assignment_id"])
            if assignment["account_id"] != payload.get("account_id"):
                raise RuntimeError("作业与账号不匹配，请重新发起操作。")
            account = self.database.get_account(payload["account_id"])
            blocked = self._assignment_preflight(account, assignment)
            if blocked:
                raise RuntimeError(blocked)
            parent, attempt = self.database.next_attempt_for_assignment(
                assignment["id"]
            )
            job = self.database.create_job(
                account["id"],
                assignment["id"],
                bool(payload.get("auto_submit", False)),
                True,
                parent_job_id=parent,
                attempt=attempt,
                trigger="manual",
            )
            self.orchestrator.wake()
            await self.orchestrator.dispatch()
            return {
                "reply": (
                    f"作业任务已进入队列：{assignment['title']} "
                    f"[{job['id'][:8]}]，默认填写并暂存。"
                ),
                "task_kind": "assignment",
                "task_id": job["id"],
            }
        task_kind = payload["task_kind"]
        task_id = payload["task_id"]
        action = payload["action"]
        if task_kind == "learning":
            if action == "delete":
                result = self.learning_service.delete_task(task_id)
                return {
                    "reply": "任务记录已删除，平台课程进度未被修改。",
                    "task": result,
                }
            result = await self.learning_service.control(task_id, action)
            return {
                "reply": f"网课任务{_action_label(action)}完成，当前状态：{result['status']}",
                "task": result,
            }
        from ..tasks import runner
        if action == "pause":
            runner.pause_job(self.database, self.learning_service.hub, task_id)
        elif action == "resume":
            runner.resume_job(self.database, self.learning_service.hub, task_id)
            self.orchestrator.wake()
            await self.orchestrator.dispatch()
        elif action == "stop":
            runner.stop_job(self.database, self.learning_service.hub, task_id)
        elif action == "retry":
            original = await runner.prepare_job_retry(self.database, task_id)
            parent, attempt = self.database.next_attempt_for_assignment(
                original["assignment_id"]
            )
            job = self.database.create_job(
                original["account_id"],
                original["assignment_id"],
                False,
                bool(original["interactive"]),
                parent_job_id=parent,
                attempt=attempt,
                trigger="manual",
            )
            self.orchestrator.wake()
            await self.orchestrator.dispatch()
            return {
                "reply": f"已创建新的作业尝试 [{job['id'][:8]}]。",
                "task_kind": "assignment",
                "task_id": job["id"],
            }
        elif action == "delete":
            account = self.database.get_account(payload["account_id"])
            result = self.database.delete_job_task(task_id)
            for identifier in result["deleted_ids"]:
                from .workspace import TaskWorkspace
                TaskWorkspace.purge_business_data_for_account(
                    account,
                    "assignment",
                    identifier,
                )
            return {"reply": "任务记录已删除，平台作业未被修改。", "task": result}
        else:
            raise ValueError("未知任务控制动作")
        current = self.database.get_job(task_id)
        return {
            "reply": f"作业任务{_action_label(action)}完成，当前状态：{current['status']}",
            "task": current,
        }

    def _issue_write_confirmation(
        self,
        channel,
        external_user_id,
        session_id,
        inbound_message_id,
        payload,
        preview,
    ):
        action_hash = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        issued = self._confirmation().issue(
            channel=channel,
            external_user_id=external_user_id,
            session_id=session_id,
            inbound_message_id=inbound_message_id,
            action_kind=payload["kind"],
            payload=payload,
            idempotency_key=(
                f"{channel}:{external_user_id}:{inbound_message_id}:{action_hash}"
            ),
        )
        expires = datetime.fromisoformat(issued["expires_at"])
        minutes = max(1, int((expires - datetime.now(timezone.utc)).total_seconds() / 60))
        return {
            "status": "confirmation_required",
            "reply": (
                f"{preview}\n确认码：{issued['token']}\n"
                f"有效期：{minutes} 分钟\n\n"
                f"回复“确认 {issued['token']}”执行，或“取消 {issued['token']}”放弃。"
            ),
            "tool": "tasks.confirm",
            "confirmation": {
                "token": issued["token"],
                "expires_at": issued["expires_at"],
            },
        }

    async def _handle_selection(
        self,
        *,
        channel,
        external_user_id,
        session_id,
        inbound_message_id,
        text,
        user,
    ):
        match = re.fullmatch(r"(?:选择\s*)?(\d+)", text.strip())
        if not match:
            return None
        session = self._memory().get_session(session_id)
        selection = session.get("metadata", {}).get("selection")
        if not isinstance(selection, dict):
            return None
        index = int(match.group(1)) - 1
        candidates = selection.get("candidates") or []
        if index < 0 or index >= len(candidates):
            return {"status": "invalid", "reply": "编号超出候选范围，请重新选择。"}
        selected = candidates[index]
        original = str(selection.get("original_text") or "")
        kind = selection.get("kind")
        self._memory().update_session_metadata(
            session_id,
            {"selection": None},
        )
        if kind == "account":
            return await self._handle_authorized(
                channel=channel,
                external_user_id=external_user_id,
                session_id=session_id,
                inbound_message_id=inbound_message_id,
                text=original,
                user=user,
                forced_account_id=selected["id"],
            )
        if kind == "course":
            account_id = selection.get("account_id")
            allowed, reason = self._auth().check(
                channel=channel,
                external_user_id=external_user_id,
                action="create",
                account_id=account_id,
            )
            if not allowed:
                return {"status": "forbidden", "reply": reason}
            course = LearningStore(self.database).course(selected["id"])
            if course["account_id"] != account_id:
                return {
                    "status": "invalid",
                    "reply": "课程与账号不匹配，请重新发起操作。",
                }
            account = self.database.get_account(account_id)
            blocked = self._learning_preflight(account, course)
            if blocked:
                return {"status": "blocked", "reply": blocked}
            payload = {
                "kind": "create_learning",
                "account_id": account_id,
                "course_id": course["id"],
            }
            preview = (
                "准备创建网课任务\n"
                f"账号：{account['name']}\n课程：{course['name']}\n"
                "模式：后台任务，沿用当前网课设置。\n"
            )
            return self._issue_write_confirmation(
                channel,
                external_user_id,
                session_id,
                inbound_message_id,
                payload,
                preview,
            )
        if kind == "assignment":
            account_id = selection.get("account_id")
            allowed, reason = self._auth().check(
                channel=channel,
                external_user_id=external_user_id,
                action="create",
                account_id=account_id,
            )
            if not allowed:
                return {"status": "forbidden", "reply": reason}
            assignment = self.database.get_assignment(selected["id"])
            if assignment["account_id"] != account_id:
                return {
                    "status": "invalid",
                    "reply": "作业与账号不匹配，请重新发起操作。",
                }
            account = self.database.get_account(account_id)
            blocked = self._assignment_preflight(account, assignment)
            if blocked:
                return {"status": "blocked", "reply": blocked}
            payload = {
                "kind": "create_assignment",
                "account_id": account_id,
                "assignment_id": assignment["id"],
                "auto_submit": False,
                "auto_evaluate": False,
            }
            preview = (
                "准备创建作业任务\n"
                f"账号：{account['name']}\n"
                f"作业：{assignment['course_name']} / {assignment['title']}\n"
                "模式：填写并暂存，不最终提交。\n"
            )
            return self._issue_write_confirmation(
                channel,
                external_user_id,
                session_id,
                inbound_message_id,
                payload,
                preview,
            )
        if kind == "task":
            purpose = selection.get("purpose")
            if purpose == "progress":
                return await self._task_progress_tool(
                    channel,
                    external_user_id,
                    session_id,
                    inbound_message_id,
                    user,
                    f"任务 {selected['id']}",
                    forced_task_id=selected["id"],
                )
            verb = selection.get("action") or _action_verb(original)
            return await self._prepare_task_action(
                verb,
                channel,
                external_user_id,
                session_id,
                inbound_message_id,
                user,
                f"{verb}任务 {selected['id']}",
            )
        return {"status": "invalid", "reply": "选择状态已失效，请重新发起请求。"}

    def _selection_reply(
        self,
        session_id,
        kind,
        candidates,
        original,
        inbound,
        *,
        purpose=None,
        extra=None,
    ):
        selection = {
            "kind": kind,
            "candidates": [
                {"id": item["id"], "label": _candidate_label(item)}
                for item in candidates
            ],
            "original_text": original,
            "inbound_message_id": inbound,
            "purpose": purpose,
            **(extra or {}),
        }
        self._memory().update_session_metadata(
            session_id,
            {"selection": selection},
        )
        lines = [f"找到 {len(candidates)} 个候选："]
        for index, item in enumerate(candidates, start=1):
            lines.append(f"{index}. {_candidate_label(item)}")
        lines.append("请回复“选择 1”或对应编号。")
        return {"status": "selection_required", "reply": "\n".join(lines)}

    def _resolve_account(
        self,
        text,
        user,
        accounts,
        *,
        forced_account_id=None,
    ):
        permitted = self._permitted_accounts(user)
        if forced_account_id:
            selected = next(
                (
                    account
                    for account in permitted
                    if account["id"] == forced_account_id
                ),
                None,
            )
            if selected is None:
                raise RuntimeError("所选账号不在当前授权范围内")
            return selected, []
        matches = [
            account for account in permitted
            if account["name"] and account["name"] in text
        ]
        if len(matches) == 1:
            return matches[0], []
        if len(matches) > 1:
            return None, matches
        if len(permitted) == 1:
            return permitted[0], []
        if not permitted:
            raise RuntimeError("当前微信用户没有可用账号范围")
        return None, permitted

    @staticmethod
    def _resolve_named_entity(text, items, label_key):
        matches = [
            item for item in items
            if str(item.get(label_key) or "") and str(item[label_key]) in text
        ]
        if len(matches) == 1:
            return matches[0], []
        if len(matches) > 1:
            return None, matches
        if len(items) == 1:
            return items[0], []
        return None, items

    def _permitted_accounts(self, user):
        accounts = self.database.list_accounts()
        allowed = set(AuthorizationService.permitted_account_ids(
            user,
            [item["id"] for item in accounts],
        ))
        return [item for item in accounts if item["id"] in allowed]

    def _permitted_tasks(self, user):
        accounts = self._permitted_accounts(user)
        account_map = {item["id"]: item for item in accounts}
        tasks = []
        for job in self.database.list_jobs(include_deleted=False):
            if job["account_id"] not in account_map:
                continue
            tasks.append({
                **job,
                "kind": "assignment",
                "title": job.get("assignment_title"),
                "account_name": account_map[job["account_id"]]["name"],
            })
        store = LearningStore(self.database)
        for run in store.runs():
            if run["account_id"] not in account_map:
                continue
            tasks.append({
                **run,
                "kind": "learning",
                "title": run.get("course_name"),
                "account_name": account_map[run["account_id"]]["name"],
            })
        return tasks

    @staticmethod
    def _resolve_task_from_text(text, tasks):
        identifiers = re.findall(r"\b[a-f0-9]{8,32}\b", text.lower())
        matches = []
        for task in tasks:
            if any(task["id"].startswith(identifier) for identifier in identifiers):
                matches.append(task)
        if not matches:
            matches = [
                task for task in tasks
                if any(
                    value and str(value) in text
                    for value in (
                        task.get("title"),
                        task.get("course_name"),
                        task.get("assignment_title"),
                    )
                )
            ]
        if len(matches) == 1:
            return matches[0], []
        if matches:
            return None, matches[:10]
        if len(tasks) == 1:
            return tasks[0], []
        return None, tasks[:10]

    def _learning_preflight(self, account, course):
        if account["status"] != "ready":
            return "账号授权已失效，请先在桌面端重新授权。"
        active = self.learning_service.store.active(account["id"])
        if active and active.get("course_id") == course["id"]:
            return "该课程已有排队或运行中的网课任务。"
        return None

    def _assignment_preflight(self, account, assignment):
        if account["status"] != "ready":
            return "账号授权已失效，请先在桌面端重新授权。"
        blocked = execution_block(assignment)
        if blocked:
            return blocked
        if not assignment.get("submittable"):
            return "该作业当前不可提交。"
        if not self.settings.openai_api_key:
            return "尚未配置可用模型。"
        if self.database.has_active_job_for_assignment(assignment["id"]):
            return "该作业已有排队或运行中的任务。"
        return None

    def _provider_health(self):
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM model_provider_health ORDER BY provider_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def _recent_message_count(self, channel, sender):
        since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) count FROM channel_messages
                WHERE channel = ? AND sender_id = ? AND created_at >= ?
                """,
                (channel, sender, since),
            ).fetchone()
        return int(row["count"])

    async def _general_answer(self, *, channel, external_user_id, session_id, user, text):
        if self.general_responder is None:
            reply = (
                "可以查询任务总况、异常、进度和剩余作业，也可以创建或控制任务。\n"
                "写操作会先给出一次性确认码。使用 /whoami 查看当前权限。"
            )
            return {"status": "ok", "reply": reply, "tool": "assistant.help"}
        package = self._memory().assemble(
            scope=(
                "global"
                if "*" in user["account_scope"]
                else f"account:{user['account_scope'][0]}"
            ),
            query=text,
            token_budget=5000,
            session_id=session_id,
            system_context={
                "identity": "Mumu Assistant",
                "role": user["role"],
                "account_scope": user["account_scope"],
                "policy": (
                    "不得声称任务已完成，除非工具结果含平台核验；"
                    "不得直接执行写操作。"
                ),
            },
        )
        reply = await self.general_responder({
            "text": text,
            "context": package.model_dump(mode="json"),
            "channel": channel,
            "external_user_id": external_user_id,
            "session_id": session_id,
        })
        return {"status": "ok", "reply": reply, "tool": "assistant.chat"}

    async def _run_tool(
        self,
        tool,
        arguments,
        operation,
        *,
        attempt_id=None,
        channel=None,
        external_user_id=None,
        idempotency_key=None,
    ):
        return await self._tools().execute(
            tool=tool,
            arguments=arguments,
            operation=operation,
            attempt_id=attempt_id,
            channel=channel,
            external_user_id=external_user_id,
            idempotency_key=idempotency_key,
        )

    async def _enqueue_unbound_reply(self, channel, external_user_id, inbound_id, text):
        self._outbox().enqueue(
            channel=channel,
            external_user_id=external_user_id,
            dedupe_key=(
                f"assistant-reply:unauthorized:{inbound_id}:"
                f"{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"
            ),
            payload={"kind": "assistant_reply", "text": text},
        )


def _looks_like_confirmation(text: str) -> bool:
    return bool(re.fullmatch(r"(?:确认|/confirm)\s*[A-Z0-9]{6}", text.strip(), re.I))


def _confirmation_token(text: str) -> str:
    return re.sub(r"^(?:确认|/confirm)\s*", "", text.strip(), flags=re.I).upper()


def _candidate_reply(kind, candidates, label_key):
    lines = [f"找到 {len(candidates)} 个“{kind}”候选："]
    for index, item in enumerate(candidates, start=1):
        label = item.get(label_key) or item.get("name") or item.get("title")
        if kind == "作业":
            label = f"{item.get('course_name')} / {label}"
        lines.append(f"{index}. {label}")
    lines.append("请回复“选择 1”或对应编号。")
    return {"status": "selection_required", "reply": "\n".join(lines)}


def _candidate_label(item):
    if "platform" in item:
        return f"{_platform_label(item['platform'])} · {item.get('name')} · 尾号 {item['id'][-4:]}"
    if "course_name" in item:
        return f"{item.get('course_name')} / {item.get('title')} · {item.get('status')}"
    return (
        f"{item.get('title') or item.get('course_name') or item['id'][:8]} "
        f"[{item.get('kind')} {item['id'][:8]}] · {item.get('status')}"
    )


def _task_status_text(task, checkpoint):
    status = task["status"]
    if task.get("submission_status") in {"submission_unknown", "submitted_unverified"}:
        result = "结果未知，需要先打开任务核对，不能直接重试。"
    elif status == "completed" and task.get("kind") == "assignment" and (
        task.get("submission_status") != "submitted_verified"
    ):
        result = "任务状态已完成，但缺少平台提交核验凭据，不能视为已完成。"
    elif status == "completed":
        result = "已完成，且已有平台核验凭据。"
    elif status == "needs_intervention":
        result = "尚未完成，等待人工审核或处理。"
    elif status == "queued":
        waiting = task.get("waiting_reason") or "等待调度资源"
        result = f"排队中：{waiting}。"
    elif status == "running":
        result = (
            f"运行中：阶段 {task.get('phase') or '未知'}，"
            f"进度 {task.get('progress', 0)}%。"
        )
    elif status == "interrupted":
        result = "已中断，需要检查原因后继续或重试。"
    elif status == "incomplete":
        result = "未完成，存在题型、素材、权限或工具限制。"
    elif status == "stopped":
        result = "已停止，请先核对平台结果再决定是否重试。"
    else:
        result = f"当前状态：{status}。"
    lines = [
        f"任务：{task.get('title') or task.get('course_name')} "
        f"[{task.get('kind')} {task['id'][:8]}]",
        result,
    ]
    if checkpoint:
        blockers = checkpoint.get("blockers") or []
        if blockers:
            lines.append(f"阻塞：{blockers[0]}")
        if checkpoint.get("next_action_id"):
            lines.append(f"下一步：{checkpoint['next_action_id']}")
    return "\n".join(lines)


def _status_reason(status, submission_status=""):
    if submission_status in {"submission_unknown", "submitted_unverified"}:
        return "提交或评测结果未知，需要先核对"
    return {
        "interrupted": "中断",
        "incomplete": "未完成",
        "failed": "失败",
    }.get(status, status)


def _action_label(action):
    return {
        "pause": "暂停",
        "resume": "继续",
        "stop": "停止",
        "retry": "重试",
        "delete": "删除",
        "create": "创建",
    }.get(action, action)


def _action_verb(text):
    if "暂停" in text:
        return "pause"
    if "继续" in text:
        return "resume"
    if "重试" in text:
        return "retry"
    if "删除" in text or "删掉" in text:
        return "delete"
    return "stop"


def _is_batch_write(text: str) -> bool:
    return any(
        word in text
        for word in ("批量", "全部", "所有", "每个")
    ) and any(word in text for word in WRITE_WORDS)


def _platform_label(platform):
    return {
        "chaoxing": "学习通",
        "educoder": "头歌",
        "icourse": "中国大学 MOOC",
        "icve": "智慧职教",
        "zjy": "职教云",
        "yuketang": "雨课堂",
        "zhihuishu": "智慧树",
    }.get(platform, platform)


def _academic_year():
    now = datetime.now()
    return now.year if now.month >= 8 else now.year - 1
