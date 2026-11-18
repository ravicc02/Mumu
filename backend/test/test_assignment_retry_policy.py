from __future__ import annotations

import unittest

from app.services.assignments.retry_policy import retryable_before_submission
from app.services.tasks.state import annotate_queues, available_job_actions


def readback_timeout_job(**overrides):
    job = {
        "status": "interrupted",
        "submission_status": "submission_unknown",
        "error": "头歌实操失败：平台回执通过，但关卡状态尚未回读为通过；已保留现场，未重复提交。",
        "progress": 75,
        "completed_units": 6,
        "total_units": 8,
        "account_status": "ready",
        "assignment_submittable": True,
        "platform": "educoder",
    }
    job.update(overrides)
    return job


class PassedReceiptReadbackTests(unittest.TestCase):
    def test_passed_receipt_with_readback_timeout_is_retryable(self):
        # 回执已明确通过，只是平台状态同步滞后；不应被当成「提交结果未知」永久卡住。
        self.assertTrue(retryable_before_submission(readback_timeout_job()))

    def test_missing_receipt_still_blocked_on_single_submission_platform(self):
        # 超星是整份作业提交：提交请求已发出但回执未知时仍必须人工核对。
        job = readback_timeout_job(
            platform="chaoxing",
            error="作业提交结果未知，已停止重交，请在平台核对。",
            submission_status="submission_unknown",
        )
        self.assertFalse(retryable_before_submission(job))

    def test_marker_without_unknown_submission_is_not_special_cased(self):
        job = readback_timeout_job(submission_status="not_submitted")
        # 已不是未知状态时无需该例外，按普通规则走（此处不是基础设施失败）。
        self.assertFalse(retryable_before_submission(job))

    def test_partial_marker_does_not_qualify(self):
        job = readback_timeout_job(
            platform="chaoxing", error="作业回执通过，正在等待结果。"
        )
        self.assertFalse(retryable_before_submission(job))

    def test_challenge_list_read_failure_before_answering_is_retryable(self):
        # 0/9 关说明一个答案都没写，即使被标成提交未知也应允许直接重试。
        job = readback_timeout_job(
            error="头歌实操失败：头歌接口核验失败：没有读取到实验关卡列表。",
            progress=0,
            completed_units=0,
        )
        self.assertTrue(retryable_before_submission(job))
        actions = available_job_actions(job, controlled=False, browser=False, retained=False)
        self.assertIn("retry", actions)

    def test_challenge_list_failure_after_answering_is_not_retryable(self):
        # 整份提交的平台仍然只放行作答前的读取失败。
        job = readback_timeout_job(
            platform="chaoxing",
            error="头歌实操失败：头歌接口核验失败：没有读取到实验关卡列表。",
            progress=60,
            completed_units=5,
        )
        self.assertFalse(retryable_before_submission(job))

    def test_educoder_answered_unknown_is_retryable(self):
        # 头歌是关卡级评测，重试会先读关卡列表并跳过已通过关卡，不构成重复提交。
        job = readback_timeout_job(
            error="头歌实操失败：评测结果尚未确认，已保留代码和提交状态，未重复提交。",
            progress=60,
            completed_units=5,
        )
        self.assertTrue(retryable_before_submission(job))
        actions = available_job_actions(job, controlled=False, browser=False, retained=False)
        self.assertIn("retry", actions)

    def test_available_actions_expose_retry_for_this_case(self):
        actions = available_job_actions(
            readback_timeout_job(), controlled=False, browser=False, retained=False
        )
        self.assertIn("retry", actions)

    def test_available_actions_hide_retry_when_receipt_missing(self):
        job = readback_timeout_job(
            platform="chaoxing",
            error="作业提交结果未知，已停止重交，请在平台核对。",
        )
        actions = available_job_actions(job, controlled=False, browser=False, retained=False)
        self.assertNotIn("retry", actions)


class ChoiceFillFailureTests(unittest.TestCase):
    LEGACY_ERROR = "头歌实操失败：选择题提交结果未知，已停止重交，请在平台核对。"

    def test_fill_structure_failure_in_message_is_retryable(self):
        # 填写阶段读到 0 道题就放弃整关，评测请求从未发出，平台没有收到提交。
        job = readback_timeout_job(
            error="头歌实操失败：选择题未填写成功，未向平台提交：选择题页面结构与平台数据不一致，"
                  "未填写：页面显示 0 道题，与读取的 3 道不一致",
            progress=11,
            completed_units=1,
        )
        self.assertTrue(retryable_before_submission(job))
        actions = available_job_actions(job, controlled=False, browser=False, retained=False)
        self.assertIn("retry", actions)

    def test_legacy_vague_message_stays_blocked(self):
        # 整份提交平台上的旧记录只有笼统文案，无法证明平台没收到提交，必须人工核对。
        job = readback_timeout_job(
            platform="chaoxing",
            error=self.LEGACY_ERROR, progress=11, completed_units=1
        )
        self.assertFalse(retryable_before_submission(job))
        actions = available_job_actions(job, controlled=False, browser=False, retained=False)
        self.assertNotIn("retry", actions)

    def test_real_missing_receipt_after_evaluate_stays_blocked(self):
        # 对照：整份提交平台已发出提交请求、只是没收到回执时，仍必须人工核对。
        job = readback_timeout_job(
            platform="chaoxing",
            error=self.LEGACY_ERROR, progress=11, completed_units=1
        )
        self.assertFalse(retryable_before_submission(job))
        actions = available_job_actions(job, controlled=False, browser=False, retained=False)
        self.assertNotIn("retry", actions)

    def test_submitted_verified_is_never_retried_by_this_rule(self):
        job = readback_timeout_job(
            error="选择题页面结构与平台数据不一致，未填写：页面显示 0 道题",
            submission_status="submitted_verified",
        )
        self.assertFalse(retryable_before_submission(job))


class RepeatableStageEvaluationTests(unittest.TestCase):
    """头歌是关卡级评测：未知提交状态直接重试，不要求人工核对。"""

    MESSAGE = "头歌实操失败：评测结果尚未确认，已保留代码和提交状态，未重复提交。"

    def test_educoder_unknown_is_retryable_in_every_stalled_status(self):
        for status in ("interrupted", "failed", "incomplete", "needs_intervention", "stopped"):
            with self.subTest(status=status):
                job = readback_timeout_job(status=status, error=self.MESSAGE)
                self.assertTrue(retryable_before_submission(job))
                actions = available_job_actions(job, controlled=False, browser=False, retained=False)
                self.assertIn("retry", actions)

    def test_educoder_submitted_unverified_is_retryable(self):
        # 头歌没有整份提交动作，重新进入会读关卡状态并跳过已通过关卡。
        job = readback_timeout_job(
            submission_status="submitted_unverified",
            error="头歌实操失败：平台已记录评测，但没有回写关卡结果。",
        )
        self.assertTrue(retryable_before_submission(job))

    def test_single_submission_platform_unknown_is_still_blocked(self):
        job = readback_timeout_job(
            platform="chaoxing",
            error="作业提交结果未知，已停止重交，请在平台核对。",
        )
        self.assertFalse(retryable_before_submission(job))
        actions = available_job_actions(job, controlled=False, browser=False, retained=False)
        self.assertNotIn("retry", actions)

    def test_completed_submission_is_never_retried(self):
        job = readback_timeout_job(submission_status="submitted_verified")
        self.assertFalse(retryable_before_submission(job))

    def test_batch_queue_keeps_retry_for_educoder_unknown(self):
        job = {
            **readback_timeout_job(error=self.MESSAGE),
            "id": "job-1",
            "account_id": "acc-1",
            "assignment_id": "asg-1",
            "created_at": "2026-11-18T19:00:00+00:00",
            "actions": ["retry", "confirm_submitted", "confirm_not_submitted"],
        }
        result = annotate_queues([job])[0]
        self.assertIn("retry", result["actions"])


if __name__ == "__main__":
    unittest.main()
