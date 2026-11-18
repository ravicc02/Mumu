from __future__ import annotations

import json
import unittest

from app.services.educoder.evaluation import EvaluationReport
from app.services.educoder.executor import ExperimentRuntime


class FakeContainer:
    def __init__(self, text, count=1):
        self.text = text
        self._count = count

    async def count(self):
        return self._count

    @property
    def first(self):
        return self

    async def inner_text(self, timeout=None):
        return self.text

    async def text_content(self, timeout=None):
        return self.text


class FakePage:
    def __init__(self, container):
        self.container = container
        self.frames = []

    def locator(self, selector):
        return self.container


def runtime_with(container):
    runtime = ExperimentRuntime.__new__(ExperimentRuntime)
    runtime.page = FakePage(container)
    return runtime


def failed_report():
    details = json.dumps(
        {"compiler": "共有1组测试集，其中有1组测试结果不匹配。", "cases": [{"id": None, "result": False, "compile_success": 1}]},
        ensure_ascii=False,
    )
    return EvaluationReport("failed", "test", "测试未通过，正在根据平台反馈修复。", details)


class PlatformOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_comparison_is_attached_for_repair(self):
        text = (
            "测试集1 消耗内存 2048 MB 代码执行时长： 0.02 秒 "
            "—— 预期输出 —— 有效等价类数量： 1 无效等价类数量： 3 "
            "—— 实际输出 —— 有效等价类数量： 4 无效等价类数量： 0"
        )
        report = await runtime_with(FakeContainer(text)).attach_platform_output(failed_report())
        payload = json.loads(report.details)
        self.assertIn("有效等价类数量： 1", payload["platform_output"])
        self.assertIn("无效等价类数量： 0", payload["platform_output"])
        self.assertEqual(payload["compiler"], "共有1组测试集，其中有1组测试结果不匹配。")
        self.assertEqual(payload["cases"][0]["result"], False)

    async def test_missing_container_keeps_original_details(self):
        original = failed_report()
        report = await runtime_with(FakeContainer("", count=0)).attach_platform_output(original)
        self.assertIs(report, original)
        self.assertEqual(json.loads(report.details)["compiler"], "共有1组测试集，其中有1组测试结果不匹配。")

    async def test_page_error_does_not_break_evaluation(self):
        class BrokenPage:
            def locator(self, selector):
                raise RuntimeError("page closed")

        runtime = ExperimentRuntime.__new__(ExperimentRuntime)
        runtime.page = BrokenPage()
        original = failed_report()
        report = await runtime.attach_platform_output(original)
        self.assertIs(report, original)

    async def test_non_failed_report_is_untouched(self):
        passed = EvaluationReport("passed", "verified_stage", "当前关卡通过回执已核验", "")
        report = await runtime_with(FakeContainer("anything")).attach_platform_output(passed)
        self.assertEqual(report.details, "")
        self.assertEqual(report.status, "passed")

    async def test_details_remains_valid_json_when_platform_returns_plain_text(self):
        report = EvaluationReport("failed", "test", "测试未通过", "plain compiler text")
        result = await runtime_with(FakeContainer("预期输出 1 实际输出 4")).attach_platform_output(report)
        payload = json.loads(result.details)
        self.assertEqual(payload["compiler"], "plain compiler text")
        self.assertIn("预期输出", payload["platform_output"])


if __name__ == "__main__":
    unittest.main()
