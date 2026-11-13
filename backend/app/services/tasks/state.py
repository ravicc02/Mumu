from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

EXECUTING_STATUSES = {"queued", "running", "paused", "stopping"}
RETRYABLE_STATUSES = {"stopped", "incomplete", "interrupted", "failed", "needs_intervention"}
REVIEWABLE_STATUSES = {"needs_intervention", "incomplete", "interrupted", "failed", "stopped"}
EVENT_PHASES = {
    "queued": "queued", "browser_starting": "browser_starting",
    "browser_ready": "browser_starting", "session_ready": "session_ready",
    "agent_starting": "agent_starting", "agent_ready": "agent_running",
    "quiz_navigating": "extracting", "quiz_extracted": "answering",
    "quiz_answers_ready": "filling", "quiz_unit_filled": "filling",
    "quiz_filled": "verifying", "thinking": "agent_running", "step": "agent_running",
    "draft_saving": "saving", "draft_saved": "verifying",
    "needs_intervention": "review", "completed": "complete", "incomplete": "incomplete",
    "interrupted": "interrupted", "stopped": "stopped",
    "educoder_opening": "extracting", "educoder_read": "filling",
    "educoder_resuming": "extracting",
    "educoder_evaluating": "evaluating", "educoder_verifying": "verifying",
    "educoder_repairing": "repairing",
    "educoder_saving": "filling",
}


def available_job_actions(job: dict[str, Any], *, controlled: bool, browser: bool, retained: bool) -> list[str]:
    status = job["status"]
    actions: list[str] = []
    if controlled:
        if status in {"queued", "running"}:
            actions.append("pause")
        elif status == "paused":
            actions.append("resume")
        if status in {"queued", "running", "paused"}:
            actions.append("stop")
    if browser and job.get("interactive"):
        actions.append("view_browser" if job.get("platform") == "educoder" else "open_browser")
    account_ready = job.get("account_status", "ready") == "ready"
    ready = account_ready and bool(job.get("assignment_submittable", True))
    if account_ready and status not in EXECUTING_STATUSES and not browser:
        actions.append("open_assignment")
    if retained and status in {"needs_intervention", "incomplete", "completed"}:
        actions.append("close_browser")
        if (status == "incomplete" or (job.get("platform") == "educoder" and status == "needs_intervention")) and browser and ready and job.get("submission_status") not in {"submission_unknown", "submitted_unverified"}:
            actions.append("continue")
    if status in RETRYABLE_STATUSES and ready:
        if job.get("submission_status") not in {"submission_unknown", "submitted_unverified"}:
            actions.append("retry")
    if status in REVIEWABLE_STATUSES:
        actions.append("confirm_submitted")
        if job.get("submission_status") in {"submission_unknown", "submitted_unverified"} or status in {"interrupted", "failed"}:
            actions.append("confirm_not_submitted")
        if job.get("submission_status") in {"filled_pending_review", "partial_pending_review"}:
            actions.append("confirm_filled")
    if status not in EXECUTING_STATUSES and not (controlled or browser or retained or job.get("browser_retained") or job.get("deleted_at")):
        actions.append("delete")
    return actions


def annotate_queues(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def created_order(item: dict[str, Any]) -> float:
        timestamp = datetime.fromisoformat(item["created_at"])
        return (timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)).timestamp()

    by_account: dict[str, list[dict[str, Any]]] = {}
    for job in sorted(jobs, key=created_order):
        by_account.setdefault(job["account_id"], []).append(job)
    result = []
    for job in jobs:
        item = {**job, "queue_position": 0, "blocked_by_job_id": None, "waiting_reason": ""}
        account_jobs = by_account[job["account_id"]]
        if job.get("assignment_id") and "actions" in item:
            attempts = [candidate for candidate in account_jobs if candidate.get("assignment_id") == job["assignment_id"]]
            if any(candidate["status"] == "completed" for candidate in attempts) or attempts[-1].get("submission_status") in {"submission_unknown", "submitted_unverified"}:
                item["actions"] = [action for action in item["actions"] if action not in {"retry", "continue"}]
            newer_active = any(candidate["id"] != job["id"] and candidate.get("assignment_id") == job["assignment_id"]
                               and (candidate["status"] in EXECUTING_STATUSES or candidate.get("browser_retained"))
                               for candidate in account_jobs)
            if newer_active:
                item["actions"] = [action for action in item["actions"] if not action.startswith("confirm_") and action not in {"retry", "continue", "delete"}]
        if job["status"] == "queued":
            item["waiting_reason"] = "正在准备独立任务浏览器"
        result.append(item)
    return result
