from __future__ import annotations

from typing import Any

AUTO_RETRY_LIMIT = 3

# 浏览器初始化、CDP 控制通道或页面读取阶段的基础设施失败。
# 这些失败发生在作答写入之前，重试不会产生重复提交。
INFRASTRUCTURE_FAILURE_MARKERS = (
    "浏览器页面",
    "页面读取",
    "截图超时",
    "cdp",
    "about:blank",
    "ax_tree",
    "dom build",
)

TERMINAL_SUBMISSION_STATUSES = {
    "filled_verified",
    "filled_pending_review",
    "submitted_verified",
    "submitted_unverified",
}


def is_infrastructure_failure(job: dict[str, Any]) -> bool:
    if job.get("status") not in {"interrupted", "failed"}:
        return False
    error = str(job.get("error") or "").lower()
    return any(marker in error for marker in INFRASTRUCTURE_FAILURE_MARKERS)


def retryable_before_submission(job: dict[str, Any]) -> bool:
    """只允许在浏览器初始化/页面读取阶段失败时自动重试。

    已填写或进入提交流程的任务即使状态未知，也必须人工核对，不能自动重提。
    """
    if not is_infrastructure_failure(job):
        return False
    if job.get("submission_status") in TERMINAL_SUBMISSION_STATUSES:
        return False
    return int(job.get("progress") or 0) <= 1 and int(job.get("completed_units") or 0) == 0
