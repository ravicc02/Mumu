from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.educoder.choices import (
    ChoiceAnswer,
    ChoiceAnswerSheet,
    answer_plan,
    build_answers,
    parse_choice_response,
    parse_choices,
)
from app.services.educoder.executor import ExperimentError, ExperimentRuntime, execute_choice_stage
from app.services.tasks.spec import educoder_task_spec


def stage_data():
    return {
        "challenge": {"id": 4838538, "subject": "条件组合覆盖", "path": None,
                      "task_pass": "理解条件组合覆盖，完成右侧选择题任务。"},
        "game": {"status": 0, "identifier": "bpflo78k5w3n"},
        "in_review": False,
        "chooses": [
            {"position": 1, "question_type": 0, "category": 2,
             "subject": "下列关于条件组合覆盖描述正确的有那些",
             "challenge_question": [
                 {"option_name": "每个条件组合至少满足一次", "position": 0},
                 {"option_name": "取真取假都满足", "position": 1},
                 {"option_name": "是很高的覆盖度准则", "position": 2},
                 {"option_name": "每个原子条件取值满足一次", "position": 3},
             ]},
            {"position": 2, "question_type": 2, "category": None, "subject": "判断一"},
            {"position": 3, "question_type": 2, "category": None, "subject": "判断二"},
        ],
    }


def mixed_stage_data():
    """第 8 关的真实形态：判断题、填空题、判断题、多选题混在同一关。"""
    return {
        "challenge": {"id": 2, "subject": "错误推断", "path": None,
                      "task_pass": "理解错误推断，完成右侧选择题任务。"},
        "game": {"status": 0, "identifier": "6rbghjw9e38i"},
        "in_review": False,
        "chooses": [
            {"position": 1, "question_type": 2, "category": None, "subject": "判断一"},
            {"position": 2, "question_type": 3, "category": None, "multi_count": 2,
             "subject": "错误推断主要依赖于测试人员的____和____"},
            {"position": 3, "question_type": 2, "category": None, "subject": "判断二"},
            {"position": 4, "question_type": 0, "category": 2, "subject": "优缺点",
             "challenge_question": [{"option_name": "A"}, {"option_name": "B"},
                                    {"option_name": "C"}, {"option_name": "D"}]},
        ],
    }


class ChoiceContractTests(unittest.TestCase):
    def test_choice_stage_routes_to_structured_without_blocker(self):
        spec = educoder_task_spec(stage_data(), instance_id="instance", challenge_id="task", paths=[], auto_submit=True)
        self.assertEqual(spec.route, "structured")
        self.assertEqual(spec.environment, "fixed_controls")
        self.assertIsNone(spec.blocker)
        self.assertTrue(spec.permissions.save)
        self.assertTrue(spec.permissions.evaluate)
        self.assertEqual(spec.model_role, "structured_answer")
        manual = educoder_task_spec(stage_data(), instance_id="instance", challenge_id="task", paths=[], auto_submit=False)
        self.assertFalse(manual.permissions.evaluate)

    def test_parse_choices_keeps_order_and_marks_multiple_and_judge(self):
        questions = parse_choices(stage_data())
        self.assertEqual([item["position"] for item in questions], [1, 2, 3])
        self.assertTrue(questions[0]["multiple"])
        self.assertEqual(questions[0]["options"][0], "每个条件组合至少满足一次")
        self.assertEqual(questions[1]["question_type"], 2)
        self.assertEqual(questions[1]["options"], ["正确", "错误"])

    def test_build_answers_rejects_out_of_range_duplicate_and_missing(self):
        questions = parse_choices(stage_data())
        good = ChoiceAnswerSheet(answers=[ChoiceAnswer(position=1, answer="ac"),
                                          ChoiceAnswer(position=2, answer="true"),
                                          ChoiceAnswer(position=3, answer="false")])
        self.assertEqual(build_answers(questions, good), ["AC", "true", "false"])
        for bad in (
            [ChoiceAnswer(position=1, answer="AE"), ChoiceAnswer(position=2, answer="true"), ChoiceAnswer(position=3, answer="false")],
            [ChoiceAnswer(position=1, answer="A"), ChoiceAnswer(position=2, answer="maybe"), ChoiceAnswer(position=3, answer="false")],
            [ChoiceAnswer(position=1, answer="AC"), ChoiceAnswer(position=2, answer="true")],
        ):
            with self.assertRaises(ValueError):
                build_answers(questions, ChoiceAnswerSheet(answers=bad))

    def test_single_choice_rejects_multiple_letters(self):
        data = stage_data()
        data["chooses"] = [{"position": 1, "question_type": 0, "category": 1,
                            "subject": "单选", "challenge_question": [{"option_name": "a"}, {"option_name": "b"}]}]
        questions = parse_choices(data)
        self.assertFalse(questions[0]["multiple"])
        with self.assertRaises(ValueError):
            build_answers(questions, ChoiceAnswerSheet(answers=[ChoiceAnswer(position=1, answer="AB")]))

    def test_parse_choices_supports_fill_blank_in_mixed_stage(self):
        questions = parse_choices(mixed_stage_data())
        self.assertEqual([item["position"] for item in questions], [1, 2, 3, 4])
        self.assertTrue(questions[1]["fill_blank"])
        self.assertEqual(questions[1]["blanks"], 2)
        self.assertEqual(questions[1]["options"], [])

    def test_build_answers_and_plan_encode_fill_blank_values(self):
        questions = parse_choices(mixed_stage_data())
        sheet = ChoiceAnswerSheet(answers=[
            ChoiceAnswer(position=1, answer="true"),
            ChoiceAnswer(position=2, answer='["直觉","经验"]'),
            ChoiceAnswer(position=3, answer="false"),
            ChoiceAnswer(position=4, answer="AC"),
        ])
        answers = build_answers(questions, sheet)
        self.assertEqual(json.loads(answers[1]), ["直觉", "经验"])
        plan = answer_plan(questions, answers)
        self.assertEqual(plan[1], ["直觉", "经验"])
        self.assertEqual(plan[3], [True, False, True, False])

    def test_fill_blank_rejects_wrong_count_empty_or_non_array(self):
        questions = parse_choices(mixed_stage_data())
        for bad in ('["直觉"]', '["直觉",""]', "不是数组", ""):
            sheet = ChoiceAnswerSheet(answers=[
                ChoiceAnswer(position=1, answer="true"),
                ChoiceAnswer(position=2, answer=bad),
                ChoiceAnswer(position=3, answer="false"),
                ChoiceAnswer(position=4, answer="AC"),
            ])
            with self.assertRaises(ValueError):
                build_answers(questions, sheet)

    def test_answer_plan_maps_letters_and_judgements_to_page_controls(self):
        questions = parse_choices(stage_data())
        plan = answer_plan(questions, ["AC", "true", "false"])
        self.assertEqual(plan, [[True, False, True, False], [True, False], [False, True]])

    def test_response_classification_uses_platform_counts_only(self):
        questions = parse_choices(stage_data())
        self.assertEqual(parse_choice_response({"game_original_status": 2, "choose_correct_num": None}, questions)["status"], "passed")
        self.assertEqual(parse_choice_response({"status": 0, "choose_correct_num": 3, "challenge_chooses_count": 3}, questions)["status"], "passed")
        failed = parse_choice_response({"status": 0, "choose_correct_num": 1, "challenge_chooses_count": 3}, questions)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["correct"], 1)
        self.assertEqual(parse_choice_response({"status": 0}, questions)["status"], "unknown")
        self.assertEqual(parse_choice_response({}, questions)["status"], "unknown")
        self.assertEqual(parse_choice_response({"status": 0, "choose_correct_num": 3, "challenge_chooses_count": 5}, questions)["status"], "unknown")
        self.assertEqual(parse_choice_response({"status": -102, "message": "请求失败"}, questions)["status"], "rejected")
        self.assertEqual(parse_choice_response(None, questions)["status"], "unknown")


class FakeControl:
    async def wait_if_paused(self):
        return None

    async def run(self, func):
        result = func()
        if asyncio.iscoroutine(result):
            return await result
        return result


class FakeDatabase:
    def __init__(self):
        self.updates = []

    def update_job(self, job_id, **fields):
        self.updates.append(fields)

    def submission_status(self):
        return [item["submission_status"] for item in self.updates if "submission_status" in item][-1]


class FakeRuntime:
    def __init__(self, receipts, game_status=0, stage_status=2):
        self.receipts = list(receipts)
        self.submitted = []
        self.game_status = game_status
        self.stage_status = stage_status
        self.task_id = "bpflo78k5w3n"
        self.filled = None
        self.evaluate_count = 0

    async def fill_choices(self, plan):
        self.filled = [list(item) for item in plan]

    async def submit_choices(self, plan, data):
        self.submitted.append([list(item) for item in plan])
        item = self.receipts.pop(0)
        if isinstance(item, Exception):
            raise item
        self.evaluate_count += 1
        if isinstance(item, dict) and (
            item.get("game_original_status") == 2
            or (item.get("choose_correct_num") is not None
                and item.get("choose_correct_num") == item.get("challenge_chooses_count"))
        ):
            self.game_status = 2
        return item

    async def task(self, *, allow_pending=False):
        return {"game": {"status": self.game_status, "evaluate_count": self.evaluate_count}, "in_review": False}

    async def challenges(self):
        return [{"identifier": self.task_id, "position": 6, "status": self.stage_status}]


async def no_emit(*args, **kwargs):
    return None


class FillFailsRuntime(FakeRuntime):
    """页面选项未渲染时，填写阶段就抛出错误，评测请求从未发出。"""

    async def fill_choices(self, plan):
        raise ExperimentError("选择题页面结构与平台数据不一致，未填写：页面显示 0 道题，与读取的 3 道不一致",
            category="choice_dom")

    async def submit_choices(self, plan, data):
        await self.fill_choices(plan)
        self.submitted.append([list(item) for item in plan])
        return {"game_original_status": 2}


class StalledReadbackRuntime(FakeRuntime):
    """回执明确通过，但平台状态迟迟不回读为 2。"""

    choice_readback_timeout = 0.05

    async def submit_choices(self, plan, data):
        self.submitted.append([list(item) for item in plan])
        return {"game_original_status": 2}

    async def task(self, *, allow_pending=False):
        return {"game": {"status": 0, "evaluate_count": 1}, "in_review": False}

    async def challenges(self):
        return [{"identifier": self.task_id, "position": 6, "status": 0}]


class FakeDiagnostics:
    def record(self, *args, **kwargs):
        return None


def llm_with(answers):
    calls = []

    async def ainvoke(messages, output_format=None, **kwargs):
        calls.append(messages)
        return SimpleNamespace(completion=ChoiceAnswerSheet(answers=[ChoiceAnswer(position=p, answer=a) for p, a in answers]))

    return SimpleNamespace(ainvoke=ainvoke, calls=calls)


def run_stage(runtime, llm, database=None, auto_submit=True):
    database = database or FakeDatabase()
    spec = educoder_task_spec(stage_data(), instance_id="instance", challenge_id="task", paths=[], auto_submit=auto_submit)
    result = asyncio.run(execute_choice_stage(
        database, {"id": "job", "auto_submit": int(auto_submit)}, runtime, llm, no_emit, FakeControl(),
        FakeDiagnostics(), stage_data(), {"name": "条件组合覆盖"}, 9, 6, spec=spec,
    ))
    return result, database


class ChoiceExecutionTests(unittest.TestCase):
    def test_passing_receipt_verifies_without_extra_readback(self):
        runtime = FakeRuntime([{"status": 0, "choose_correct_num": 3, "challenge_chooses_count": 3}])
        result, database = run_stage(runtime, llm_with([(1, "AC"), (2, "true"), (3, "false")]))
        self.assertIsNone(result)
        self.assertEqual(runtime.submitted, [[[True, False, True, False], [True, False], [False, True]]])
        self.assertEqual(database.submission_status(), "submitted_verified")

    def test_failed_receipt_retries_once_and_recovers(self):
        runtime = FakeRuntime([
            {"status": 0, "choose_correct_num": 2, "challenge_chooses_count": 3},
            {"status": 0, "choose_correct_num": 3, "challenge_chooses_count": 3},
        ])
        llm = llm_with([(1, "AC"), (2, "true"), (3, "false")])
        result, database = run_stage(runtime, llm)
        self.assertIsNone(result)
        self.assertEqual(len(runtime.submitted), 2)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("previous_attempt", llm.calls[1][1].content)
        self.assertEqual(database.submission_status(), "submitted_verified")

    def test_repeated_failures_stop_without_claiming_success(self):
        runtime = FakeRuntime([{"status": 0, "choose_correct_num": 1, "challenge_chooses_count": 3}] * 3)
        with self.assertRaises(ExperimentError) as failure:
            run_stage(runtime, llm_with([(1, "AC"), (2, "true"), (3, "false")]))
        self.assertEqual(failure.exception.category, "choice_incorrect")
        self.assertFalse(failure.exception.unknown)
        self.assertEqual(len(runtime.submitted), 3)

    def test_fill_failure_before_evaluate_is_not_marked_unknown(self):
        # 填写发生在评测请求之前，平台没有收到提交，不能记成「提交结果未知」。
        database = FakeDatabase()
        runtime = FillFailsRuntime([])
        with self.assertRaises(ExperimentError) as failure:
            run_stage(runtime, llm_with([(1, "AC"), (2, "true"), (3, "false")]), database)
        self.assertTrue(failure.exception.interrupted)
        self.assertFalse(failure.exception.unknown)
        self.assertEqual(failure.exception.category, "choice_pre_submit")
        self.assertEqual(database.submission_status(), "not_submitted")
        self.assertEqual(len(runtime.submitted), 0)

    def test_transport_failure_is_marked_unknown_and_not_retried(self):
        runtime = FakeRuntime([RuntimeError("network down")])
        with self.assertRaises(ExperimentError) as failure:
            run_stage(runtime, llm_with([(1, "AC"), (2, "true"), (3, "false")]))
        self.assertTrue(failure.exception.unknown)
        self.assertEqual(failure.exception.category, "choice_submission_unknown")
        self.assertEqual(len(runtime.submitted), 1)

    def test_unknown_receipt_reads_back_game_status_before_claiming(self):
        runtime = FakeRuntime([{"status": 0}], game_status=2)
        result, database = run_stage(runtime, llm_with([(1, "AC"), (2, "true"), (3, "false")]))
        self.assertIsNone(result)
        self.assertEqual(database.submission_status(), "submitted_verified")

    def test_manual_mode_fills_choices_without_evaluating(self):
        runtime = FakeRuntime([])
        result, database = run_stage(runtime, llm_with([(1, "AC"), (2, "true"), (3, "false")]), auto_submit=False)
        self.assertEqual(result["status"], "needs_intervention")
        self.assertEqual(result["submission_status"], "filled_pending_review")
        self.assertEqual(runtime.submitted, [])
        self.assertEqual(runtime.filled, [[True, False, True, False], [True, False], [False, True]])
        self.assertNotIn("submitted_unverified", database.updates[-1].get("submission_status", ""))

    def test_unpassed_receipt_with_new_evaluation_count_retries_instead_of_hanging_unknown(self):
        # Choice stages expose no per-question result; a new evaluate count is the failure signal.
        runtime = FakeRuntime([{"status": 0}] * 3)
        with self.assertRaises(ExperimentError) as failure:
            run_stage(runtime, llm_with([(1, "AC"), (2, "true"), (3, "false")]))
        self.assertEqual(failure.exception.category, "choice_incorrect")
        self.assertFalse(failure.exception.unknown)
        self.assertEqual(len(runtime.submitted), 3)

    def test_invalid_model_answer_is_not_submitted(self):
        runtime = FakeRuntime([{"status": 0, "choose_correct_num": 3, "challenge_chooses_count": 3}])
        with self.assertRaises(ExperimentError) as failure:
            run_stage(runtime, llm_with([(1, "AE"), (2, "true"), (3, "false")]))
        self.assertEqual(failure.exception.category, "choice_incorrect")
        self.assertEqual(runtime.submitted, [])

    def test_mixed_stage_with_fill_blank_executes_without_unsupported_error(self):
        data = mixed_stage_data()
        runtime = FakeRuntime([{"status": 0, "choose_correct_num": 4, "challenge_chooses_count": 4}])
        llm = llm_with([(1, "true"), (2, '["直觉","经验"]'), (3, "false"), (4, "AC")])
        database = FakeDatabase()
        spec = educoder_task_spec(data, instance_id="instance", challenge_id="task", paths=[], auto_submit=True)
        result = asyncio.run(execute_choice_stage(
            database, {"id": "job", "auto_submit": 1}, runtime, llm, no_emit, FakeControl(),
            FakeDiagnostics(), data, {"name": "错误推断"}, 8, 7, spec=spec,
        ))
        self.assertIsNone(result)
        self.assertEqual(runtime.submitted[0][1], ["直觉", "经验"])
        self.assertEqual(database.submission_status(), "submitted_verified")

    def test_passed_receipt_with_stalled_readback_stays_retryable(self):
        # 回执已明确通过时，回读超时只是平台状态同步滞后，不能记成「提交结果未知」，
        # 否则任务会被永久卡住、连已通过的关卡都无法继续。
        database = FakeDatabase()
        runtime = StalledReadbackRuntime([])
        with self.assertRaises(ExperimentError) as failure:
            run_stage(runtime, llm_with([(1, "AC"), (2, "true"), (3, "false")]), database)
        self.assertEqual(failure.exception.category, "choice_readback")
        self.assertFalse(failure.exception.unknown)
        self.assertEqual(database.submission_status(), "not_submitted")

    def test_missing_receipt_still_marked_unknown_and_not_retried(self):
        # 对照：提交请求抛出、没有回执时，仍必须是「提交结果未知」，不允许自动重试。
        database = FakeDatabase()
        runtime = FakeRuntime([RuntimeError("network down")])
        with self.assertRaises(ExperimentError) as failure:
            run_stage(runtime, llm_with([(1, "AC"), (2, "true"), (3, "false")]), database)
        self.assertTrue(failure.exception.unknown)
        self.assertEqual(failure.exception.category, "choice_submission_unknown")


class FakePage:
    def __init__(self, results):
        self.results = list(results)
        self.reads = 0

    async def evaluate(self, script, plan):
        self.reads += 1
        return self.results.pop(0)

    async def wait_for_timeout(self, ms):
        return None


class ChoicePlanReadTests(unittest.TestCase):
    def test_zero_questions_is_reread_before_failing(self):
        # 题目列表延迟渲染时先读到 0 道，不应立即放弃整关。
        page = FakePage([
            {"ok": False, "count": 0, "reason": "页面显示 0 道题，与读取的 3 道不一致"},
            {"ok": True, "count": 3},
        ])
        runtime = object.__new__(ExperimentRuntime)
        result = asyncio.run(runtime._apply_choice_plan(page, [[True], [True], [False]], interval=0))
        self.assertEqual(result, {"ok": True, "count": 3})
        self.assertEqual(page.reads, 2)

    def test_non_zero_mismatch_fails_without_rereading(self):
        # 非 0 的数量不符是数据与页面不一致，重复读取不会修复，应立即报告。
        page = FakePage([{"ok": False, "count": 2, "reason": "页面显示 2 道题，与读取的 3 道不一致"}])
        runtime = object.__new__(ExperimentRuntime)
        result = asyncio.run(runtime._apply_choice_plan(page, [[True], [True], [False]], interval=0))
        self.assertFalse(result["ok"])
        self.assertEqual(page.reads, 1)


if __name__ == "__main__":
    unittest.main()
