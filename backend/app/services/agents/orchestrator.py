from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from ...preferences import load_preferences
from ..learning.store import LearningStore


logger = logging.getLogger(__name__)


def _timestamp(value: str | None) -> float:
    if not value:
        return 0
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class TaskOrchestrator:
    """Single-process scheduler backed by persistent attempts and SQLite leases."""

    def __init__(
        self,
        database_provider: Callable[[], Any],
        settings_provider: Callable[[], Any],
        *,
        assignment_starter: Callable[[str, Any], None],
        learning_starter: Callable[[str], None],
        candidate_guard: Callable[[str, dict[str, Any]], str | None] | None = None,
    ) -> None:
        self.database_provider = database_provider
        self.settings_provider = settings_provider
        self.assignment_starter = assignment_starter
        self.learning_starter = learning_starter
        self.candidate_guard = candidate_guard
        self._loop = None
        self._dispatch_lock: asyncio.Lock | None = None
        self._wake_event: asyncio.Event | None = None
        self._shutdown = asyncio.Event()

    @property
    def database(self):
        return self.database_provider()

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        self._loop = loop
        self._dispatch_lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._shutdown = asyncio.Event()

    def wake(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._loop is not loop:
            self._bind_loop()
        if self._wake_event:
            self._wake_event.set()

    async def shutdown(self) -> None:
        self._bind_loop()
        self._shutdown.set()
        if self._wake_event:
            self._wake_event.set()

    def recover(self) -> None:
        database = self.database
        database.cleanup_expired_resource_leases()
        for job in database.list_jobs(include_deleted=False):
            if job["status"] == "queued":
                database.release_resource_leases(job["id"])
        try:
            store = LearningStore(database)
            for run in store.runs():
                if run["status"] == "queued":
                    database.release_resource_leases(run["id"])
        except Exception:
            logger.debug("Learning queue recovery skipped", exc_info=True)

    async def run_forever(self) -> None:
        self._bind_loop()
        while not self._shutdown.is_set():
            try:
                await self.dispatch()
            except Exception:
                logger.exception("Task orchestrator dispatch failed")
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=1)
                self._wake_event.clear()
            except TimeoutError:
                pass

    async def dispatch(self) -> dict[str, Any]:
        self._bind_loop()
        async with self._dispatch_lock:
            database = self.database
            database.cleanup_expired_resource_leases()
            jobs = [
                job for job in database.list_jobs(include_deleted=False)
                if job["status"] == "queued"
            ]
            learning_store = LearningStore(database)
            try:
                learning_runs = [
                    run for run in learning_store.runs()
                    if run["status"] == "queued"
                ]
            except Exception:
                learning_runs = []

            all_jobs = database.list_jobs(include_deleted=False)
            running_jobs = sum(job["status"] == "running" for job in all_jobs)
            retained_jobs = sum(bool(job.get("browser_retained")) for job in all_jobs)
            try:
                active_learning = [
                    run for run in learning_store.runs()
                    if run["status"] in {"running"}
                ]
            except Exception:
                active_learning = []
            leases = database.list_resource_leases()
            slot_leases = {
                lease["resource_key"]: lease
                for lease in leases
                if str(lease["resource_key"]).startswith("browser-slot:")
            }
            leased_attempts = {
                str(lease["resource_key"]).split(":", 1)[1]
                for lease in leases
                if str(lease["resource_key"]).startswith("browser:")
            }
            try:
                limit = load_preferences(database).system.browser_task_concurrency
            except Exception:
                limit = 2
            occupied = max(len(slot_leases), running_jobs + len(active_learning)) + retained_jobs
            available = max(0, int(limit) - occupied)
            free_slots = [
                index
                for index in range(int(limit))
                if f"browser-slot:{index}" not in slot_leases
            ][:available]

            candidates: list[dict[str, Any]] = []
            account_running: dict[str, int] = {}
            for job in all_jobs:
                if job["status"] in {"running", "paused", "stopping"}:
                    account_running[job["account_id"]] = account_running.get(job["account_id"], 0) + 1
            for run in active_learning:
                account_running[run["account_id"]] = account_running.get(run["account_id"], 0) + 1
            candidates.extend({
                "kind": "assignment",
                "id": job["id"],
                "account_id": job["account_id"],
                "priority": int(job.get("queue_priority") or 0),
                "created_at": job.get("created_at"),
                "not_before": job.get("not_before"),
                "row": job,
            } for job in jobs)
            candidates.extend({
                "kind": "learning",
                "id": run["id"],
                "account_id": run["account_id"],
                "priority": int(run.get("queue_priority") or 0),
                "created_at": run.get("created_at"),
                "not_before": run.get("not_before"),
                "row": run,
            } for run in learning_runs)
            candidates = [
                item for item in candidates
                if item["id"] not in leased_attempts
                and _timestamp(item["not_before"]) <= datetime.now(timezone.utc).timestamp()
            ]
            candidates = self._fair_candidates(candidates, account_running)

            started: list[str] = []
            waiting = 0
            for candidate in candidates:
                if not free_slots:
                    if self._set_waiting(candidate, "等待全局浏览器任务槽位"):
                        waiting += 1
                    continue
                guard_reason = None
                if self.candidate_guard:
                    try:
                        guard_reason = self.candidate_guard(candidate["kind"], candidate["row"])
                    except Exception:
                        logger.exception(
                            "Task orchestrator guard failed for %s",
                            candidate["id"],
                        )
                        guard_reason = "等待调度预检"
                if guard_reason:
                    self._set_waiting(candidate, guard_reason)
                    waiting += 1
                    continue

                slot = free_slots.pop(0)
                resource_keys = [f"browser-slot:{slot}", f"browser:{candidate['id']}"]
                if candidate["kind"] == "assignment":
                    resource_keys.append(f"assignment:{candidate['row']['assignment_id']}")
                    try:
                        assignment = database.get_assignment(
                            candidate["row"]["assignment_id"]
                        )
                        assessment = assignment.get("assessment")
                    except KeyError:
                        assessment = None
                    if isinstance(assessment, dict):
                        instance_id = assessment.get("instance_id") or assessment.get("runtime_instance_id")
                        if instance_id:
                            resource_keys.append(f"remote-instance:{instance_id}")
                    owner_kind = "assignment"
                else:
                    resource_keys.append(f"course:{candidate['row']['course_id']}")
                    owner_kind = "learning"
                acquired, conflict = database.acquire_resource_leases(
                    owner_id=candidate["id"],
                    owner_kind=owner_kind,
                    resource_keys=resource_keys,
                    ttl_seconds=60,
                )
                if not acquired:
                    free_slots.insert(0, slot)
                    self._set_waiting(candidate, f"等待资源：{conflict or '其他任务'}", delay_seconds=2)
                    waiting += 1
                    continue
                self._clear_waiting(candidate)
                try:
                    settings = self.settings_provider()
                    if candidate["kind"] == "assignment":
                        self.assignment_starter(candidate["id"], settings)
                    else:
                        self.learning_starter(candidate["id"])
                except Exception as error:
                    database.release_resource_leases(candidate["id"])
                    free_slots.insert(0, slot)
                    self._set_waiting(
                        candidate,
                        f"任务启动失败，{type(error).__name__}",
                        delay_seconds=5,
                    )
                    logger.exception("Task orchestrator could not start %s", candidate["id"])
                    waiting += 1
                    continue
                started.append(candidate["id"])

            return {
                "started": started,
                "waiting": waiting,
                "limit": limit,
                "available_slots": available,
            }

    def _set_waiting(
        self,
        candidate: dict[str, Any],
        reason: str,
        *,
        delay_seconds: int = 0,
    ) -> bool:
        row = candidate["row"]
        if row.get("waiting_reason") == reason and (row.get("phase") == "waiting_resource"):
            return False
        not_before = None
        if delay_seconds:
            from datetime import timedelta
            not_before = (
                datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
            ).isoformat()
        fields = {
            "phase": "waiting_resource",
            "waiting_reason": reason,
            "not_before": not_before,
        }
        try:
            self.database.save_task_checkpoint(
                attempt_id=candidate["id"],
                attempt_kind=candidate["kind"],
                phase="waiting_resource",
                goal=(
                    {
                        "assignment": row.get("assignment_title"),
                        "course": row.get("course_name"),
                    }
                    if candidate["kind"] == "assignment"
                    else {
                        "course": row.get("course_name"),
                        "platform": row.get("platform"),
                    }
                ),
                completed={
                    "progress": row.get("progress", 0),
                },
                blockers=[reason],
                next_action_id="resource_retry",
            )
        except Exception:
            logger.exception(
                "Task queue checkpoint failed for %s",
                candidate["id"],
            )
        if candidate["kind"] == "assignment":
            self.database.update_job(candidate["id"], **fields)
        else:
            LearningStore(self.database).update(candidate["id"], **fields)
        return True

    @staticmethod
    def _fair_candidates(
        candidates: list[dict[str, Any]],
        account_running: dict[str, int],
    ) -> list[dict[str, Any]]:
        grouped: dict[int, dict[str, list[dict[str, Any]]]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate["priority"], {}).setdefault(
                candidate["account_id"],
                [],
            ).append(candidate)
        result: list[dict[str, Any]] = []
        for priority in sorted(grouped, reverse=True):
            accounts = grouped[priority]
            for items in accounts.values():
                items.sort(
                    key=lambda item: (
                        _timestamp(item["created_at"]),
                        item["id"],
                    )
                )
            account_order = sorted(
                accounts,
                key=lambda account_id: (
                    account_running.get(account_id, 0),
                    account_id,
                ),
            )
            while any(accounts[account_id] for account_id in account_order):
                for account_id in account_order:
                    if accounts[account_id]:
                        result.append(accounts[account_id].pop(0))
        return result

    def _clear_waiting(self, candidate: dict[str, Any]) -> None:
        fields = {
            "phase": "starting",
            "waiting_reason": None,
            "not_before": None,
        }
        if candidate["kind"] == "assignment":
            self.database.update_job(candidate["id"], **fields)
        else:
            LearningStore(self.database).update(candidate["id"], **fields)
