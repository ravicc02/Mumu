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
    # 关卡列表在作答前读取失败：一个答案都没写，重试不会产生重复提交。
    "没有读取到实验关卡列表",
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


# 平台回执已明确通过、只是关卡状态回读超时的专属文案。
# 这不属于「提交请求发出但没收到回执」：重试时已通过的关卡会跳过，
# 未通过的关卡重做也没有重复提交风险。
PASSED_RECEIPT_READBACK_MARKERS = ("回执通过", "尚未回读为通过")


def has_passed_receipt_readback_timeout(job: dict[str, Any]) -> bool:
    if job.get("status") != "interrupted":
        return False
    if job.get("submission_status") not in {"submission_unknown", "submitted_unverified"}:
        return False
    error = str(job.get("error") or "")
    return all(marker in error for marker in PASSED_RECEIPT_READBACK_MARKERS)


# 选择题在页面填写阶段失败：选项未渲染就放弃整关，评测请求从未发出。
# 这条文案由当前代码写入，明确包含页面结构原因，可以据此确认平台从未收到提交。
CHOICE_FILL_FAILURE_MARKERS = ("选择题页面结构与平台数据不一致", "选择题未填写成功")


def has_choice_fill_failure(job: dict[str, Any]) -> bool:
    if job.get("status") != "interrupted":
        return False
    if job.get("submission_status") not in {"submission_unknown", "submitted_unverified"}:
        return False
    error = str(job.get("error") or "")
    return any(marker in error for marker in CHOICE_FILL_FAILURE_MARKERS)


# 头歌的「提交」是关卡级评测：每次评测只针对当前关卡，可重复执行；
# 整份作业的完成状态由平台按关卡通过情况判定，没有一次性整份提交动作。
# 重试时会先读取关卡列表，已通过的关卡直接跳过，因此评测回执是否确认
# 不构成重复提交风险，不需要人工核对即可直接重试。
REPEATABLE_EVALUATION_PLATFORMS = {"educoder"}

REPEATABLE_RETRY_STATUSES = {"interrupted", "failed", "incomplete", "needs_intervention", "stopped"}


def has_repeatable_stage_evaluation(job: dict[str, Any]) -> bool:
    if job.get("platform") not in REPEATABLE_EVALUATION_PLATFORMS:
        return False
    if job.get("status") not in REPEATABLE_RETRY_STATUSES:
        return False
    return job.get("submission_status") in {"submission_unknown", "submitted_unverified"}


def retryable_before_submission(job: dict[str, Any]) -> bool:
    """只允许在浏览器初始化/页面读取阶段失败时自动重试。

    已填写或进入提交流程的任务即使状态未知，也必须人工核对，不能自动重提。
    例外一：关卡级评测平台（头歌），评测可重复、已通过关卡会跳过，未知状态可直接重试。
    例外二：平台回执已明确通过，仅关卡状态回读超时，可按已通过关卡继续重试。
    例外三：选择题在页面填写阶段失败，错误文案已明确说明评测请求从未发出。
    """
    if has_repeatable_stage_evaluation(job):
        return True
    if has_passed_receipt_readback_timeout(job):
        return True
    if has_choice_fill_failure(job):
        return True
    if not is_infrastructure_failure(job):
        return False
    if job.get("submission_status") in TERMINAL_SUBMISSION_STATUSES:
        return False
    return int(job.get("progress") or 0) <= 1 and int(job.get("completed_units") or 0) == 0
