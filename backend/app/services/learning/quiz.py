from __future__ import annotations

import asyncio
import json
from typing import Any

from ..assignments.quiz_adapter import QuizAnswerSheet, generate_answers
from ..assignments.readiness import assess_questions


class LearningQuizError(RuntimeError):
    pass


def quiz_signature(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": str(unit.get("type") or ""),
            "text": str(unit.get("text") or ""),
            "options": [str(option.get("label") or "") for option in unit.get("options") or []],
        }
        for unit in extracted.get("units") or []
    ]


def _answer_map(extracted: dict[str, Any], answer_sheet: QuizAnswerSheet) -> dict[str, Any]:
    expected = [str(unit["unit_id"]) for unit in extracted.get("units") or []]
    answers = {answer.unit_id: answer for answer in answer_sheet.answers}
    if len(expected) != len(set(expected)):
        raise LearningQuizError("页面题号重复，无法把答案安全对应到每一道题")
    if set(expected) != set(answers) or len(expected) != len(answer_sheet.answers):
        missing = sorted(set(expected) - set(answers))
        detail = "缺少 " + "、".join(missing[:8]) if missing else "包含重复或未知作答单元"
        if len(answer_sheet.answers) != len(answers):
            detail = "包含重复作答单元"
        raise LearningQuizError(f"模型答案与章节题号无法一一对应：{detail}")
    return answers


def _verify_filled(extracted: dict[str, Any], filled: list[dict[str, Any]], answers: dict[str, Any]) -> list[str]:
    by_id = {str(item.get("unit_id")): item for item in filled}
    invalid = []
    for unit in extracted.get("units") or []:
        unit_id = str(unit["unit_id"])
        result = by_id.get(unit_id)
        if result is None:
            invalid.append(unit_id)
            continue
        expected = [str(value).strip() for value in answers[unit_id].values]
        if unit["kind"] in {"single", "multiple"}:
            checked = [str(value).strip() for value in result.get("checked") or []]
            needs_hidden = unit.get("verification", "hidden") == "hidden"
            if sorted(checked) != sorted(expected) or (needs_hidden and not result.get("hidden_value")):
                invalid.append(unit_id)
        elif unit["kind"] == "shared":
            try:
                actual = [str(item["content"]).strip() for item in json.loads(result.get("hidden_value") or "[]")]
            except (KeyError, TypeError, ValueError):
                actual = []
            if not result.get("hidden_value") or actual != expected:
                invalid.append(unit_id)
        else:
            field_values = [str(value).strip() for value in result.get("field_values") or []]
            if (
                len(field_values) != len(unit.get("fields") or [])
                or not field_values
                or field_values != expected
            ):
                invalid.append(unit_id)
    return invalid


async def solve_learning_quiz(
    *,
    browser: Any,
    frame: Any,
    unit: dict[str, Any],
    llm: Any,
    control: Any,
    emit: Any,
    auto_submit: bool,
    diagnostics: Any = None,
) -> dict[str, Any]:
    await emit("quiz_navigating", f"正在读取章节题目：{unit['title']}")
    extracted: dict[str, Any] = {}
    for attempt in range(4):
        extracted = await control.run(lambda: browser.extract_quiz_questions(frame))
        if int(extracted.get("question_count") or 0) > 0:
            break
        if attempt < 3:
            await control.run(lambda: asyncio.sleep(.6 if attempt == 0 else 1))
    question_count = int(extracted.get("question_count") or 0)
    unit_count = int(extracted.get("unit_count") or 0)
    if question_count <= 0 or unit_count <= 0:
        return {
            "status": "unsupported",
            "reason": "没有识别到受支持的章节题目控件",
            "question_count": question_count,
            "unit_count": unit_count,
            "signature": quiz_signature(extracted),
        }

    if diagnostics:
        diagnostics.snapshot("learning-quiz-questions", extracted, content=True)
    await emit(
        "quiz_extracted",
        f"已读取 {question_count} 道题、{unit_count} 个作答单元",
        {"question_count": question_count, "unit_count": unit_count},
    )

    assessment = assess_questions(extracted)
    if assessment["execution_state"] == "manual_required":
        blocked = assessment.get("blocked_units") or []
        reason = "；".join(
            f"第 {item.get('question_no', '?')} 题：{item.get('reason', '需要人工材料')}"
            for item in blocked[:6]
        ) or "章节题目包含需要人工材料的题型"
        await emit("quiz_manual_required", reason, {"assessment": assessment})
        return {
            "status": "unsupported",
            "reason": reason,
            "question_count": question_count,
            "unit_count": unit_count,
            "signature": quiz_signature(extracted),
        }

    try:
        answer_sheet = await control.run(
            lambda: asyncio.wait_for(generate_answers(llm, extracted), timeout=300)
        )
    except TimeoutError as error:
        raise LearningQuizError("章节题目模型在 5 分钟内没有返回全部答案") from error
    answers = _answer_map(extracted, answer_sheet)
    if diagnostics:
        diagnostics.snapshot("learning-quiz-answers", answer_sheet.model_dump(mode="json"), content=True)
    await emit(
        "quiz_answers_ready",
        f"已生成 {len(answers)} 个作答单元的答案",
        {"answer_count": len(answers)},
    )

    filled = []
    for index, answer in enumerate(answer_sheet.answers):
        result = await control.run(
            lambda answer=answer: browser.fill_quiz_answers(
                frame,
                QuizAnswerSheet(answers=[answer]),
            )
        )
        filled.extend(result)
        await emit(
            "quiz_unit_filled",
            f"已填写 {index + 1}/{unit_count} 个作答单元",
            {
                "unit_id": answer.unit_id,
                "filled_count": index + 1,
                "total_units": unit_count,
            },
        )

    invalid = _verify_filled(extracted, filled, answers)
    if invalid:
        if diagnostics:
            diagnostics.snapshot(
                "learning-quiz-verification-failed",
                {"invalid_ids": invalid, "filled": filled},
                content=True,
            )
        raise LearningQuizError(
            "以下章节作答单元没有通过页面值核验：" + "、".join(invalid[:8])
        )
    await emit(
        "quiz_filled",
        f"全部 {unit_count} 个作答单元已写入并通过页面核验",
        {"filled_count": len(filled), "unit_count": unit_count},
    )

    if not auto_submit:
        return {
            "status": "filled_pending_review",
            "question_count": question_count,
            "unit_count": unit_count,
            "filled_count": len(filled),
            "signature": quiz_signature(extracted),
        }

    submission = await control.run(lambda: browser.submit_quiz_answers(frame))
    if submission.get("failed"):
        raise LearningQuizError(
            submission.get("reason") or "平台没有接受本次章节题目提交"
        )
    try:
        still_open = await control.run(lambda: browser.quiz_is_open(frame))
    except Exception:
        still_open = None
    if submission.get("clicked") or submission.get("confirmed") or still_open is False:
        confirmed = bool(submission.get("confirmed"))
        await emit(
            "quiz_submitted",
            "章节题目已提交，平台已确认" if confirmed else "章节题目已提交，等待平台完成状态确认",
            {
                "confirmed": confirmed,
                "pending": not confirmed and (bool(submission.get("pending")) or still_open is False),
                "reason": submission.get("reason") or "",
            },
        )
        return {
            "status": "submitted",
            "question_count": question_count,
            "unit_count": unit_count,
            "filled_count": len(filled),
            "submission": submission,
            "signature": quiz_signature(extracted),
        }
    if still_open is None:
        return {
            "status": "filled_pending_review",
            "reason": "提交动作已执行，但无法确认题目页面状态；请在实时浏览器核对，避免重复提交",
            "question_count": question_count,
            "unit_count": unit_count,
            "filled_count": len(filled),
            "submission": submission,
            "signature": quiz_signature(extracted),
        }
    return {
        "status": "unsupported",
        "reason": submission.get("reason") or "题目已填写，但没有找到可核验的提交入口",
        "question_count": question_count,
        "unit_count": unit_count,
        "filled_count": len(filled),
        "signature": quiz_signature(extracted),
    }
