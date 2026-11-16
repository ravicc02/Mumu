from __future__ import annotations

import re

from ...db import utc_now


READINESS_POLICY_VERSION = 3


def _prompt(unit: dict) -> str:
    value = unit.get("prompt_text", unit.get("text"))
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^\s*\d+[.、．]\s*", "", text)
    text = re.sub(r"[（(【\[]?(?:单选题|多选题|判断题|填空题|简答题|论述题|共用选项题)[）)】\]]?", "", text)
    return re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*分[）)]", "", text).strip()


def unit_requirements(unit: dict) -> list[str]:
    text = _prompt(unit)
    # Delivery instructions need an action and a deliverable, not a subject keyword.
    clauses = re.split(r"[。！？!?；;\n]", text)
    instructions = []
    for clause in clauses:
        if re.search(r"无需|不需要|不必|不用|可选|选传|选填|非必填", clause):
            continue
        theory = re.search(r"(?:简述|解释|什么是|如何|说明).*(?:报告|汇报|代码|源程序|源码|文件|视频|PPT)", clause, re.I)
        if theory or re.search(r"为什么|怎样|有几种|有哪些|哪种", clause):
            if not re.search(r"(?:并|同时|另需|还需).*(?:上传|上交|交付|提交).{0,60}(?:报告|材料|文件|附件|代码|源码|压缩包|PPT|视频)", clause, re.I):
                continue
        if re.search(r"(?:提交|上传|上交|交付|撰写|写一份|写一篇|写个|写份|制作|准备|录制|拍摄|完成|编写|设计|绘制|画出|做一份|做个|做一个|汇报).+", clause):
            instructions.append(clause)
    demanded = "；".join(instructions)
    required = []
    for label, pattern in (
        ("实验或实践报告", r"(?:实验|实践|实训|课程设计).{0,15}报告"),
        ("报告或汇报材料", r"报告|汇报|调研材料|论文"),
        ("PPT 演示文稿", r"(?<![a-z])pptx?(?![a-z])|幻灯片|演示文稿"),
        ("项目源码", r"源程序|源码|项目代码|程序代码|(?:提交|上传|交付).{0,40}代码"),
        ("演示视频", r"视频|录像|\.mp4\b"),
        ("压缩包", r"压缩包|\.(?:rar|zip|7z)\b"),
        ("指定文件", r"(?:提交|上传|上交|交付).{0,80}(?:文件|附件|材料|\.(?:docx?|xlsx?|pptx?|pdf)\b)"),
        ("图形或设计文件", r"(?:绘制|画出|提交|上传).{0,25}(?:状态机图|活动图|顺序图|用例图|类图|流程图|ER图)"),
    ):
        if re.search(pattern, demanded, re.I):
            required.append(label)
    if "实验或实践报告" in required and "报告或汇报材料" in required:
        required.remove("报告或汇报材料")
    has_text_controls = bool(unit.get("fields") or unit.get("options") or unit.get("groups"))
    file_only = unit.get("file_inputs") and not has_text_controls
    if unit.get("required_file_inputs") or (file_only and not unit.get("optional_file_inputs")):
        required.append("上传文件")
    return list(dict.fromkeys(required))


def _question_issue(unit: dict) -> str:
    text = _prompt(unit)
    if not text or not re.search(r"[\w\u4e00-\u9fff]", text):
        return "未读取到明确题目"
    ordinal = r"[0-9０-９一二三四五六七八九十百零〇两]+"
    task_label = r"(?:实验|实训|实践|作业|练习|测验|测试|任务)"
    if re.fullmatch(
        rf"(?:第\s*{ordinal}\s*(?:次|个)?\s*{task_label}|{task_label}(?:\s*第?\s*{ordinal}\s*(?:次|题)?)?)[。.!！:：\s]*",
        text,
    ):
        return "仅有任务标题，缺少具体题目或要求"
    if re.fullmatch(r"(?:请)?(?:见附件|详见附件|同上|略|待定|暂无|无|按要求完成(?:本次作业|作业)?|完成(?:本次)?作业|自行完成|请作答)[。.!！\s]*", text):
        return "题目表述不清或缺少具体要求"
    if re.search(r"通知|温馨提示|请同学们|请各位同学|收到请回复|请回复收到|本周无作业|无需作答|不需要作答", text):
        if not re.search(r"(?:回答|分析|简述|说明|解释|比较|为什么|什么是|计算(?!机)|求出|选择|填空|下列|以下).+", text):
            return "通知式作业，没有具体作答任务"
    if re.search(r"(?:作业|题目|问题|要求).{0,8}(?:见|详见|参见)附件|按.{0,10}附件.{0,10}要求|附件.{0,10}要求", text):
        return "具体题目或要求仅在附件中，无法确认"
    return ""


def _controls_complete(unit: dict) -> bool:
    kind = unit.get("kind")
    if kind in {"single", "multiple"}:
        return bool(unit.get("options"))
    if kind == "shared":
        groups = unit.get("groups") or []
        return bool(groups) and all(group.get("options") for group in groups)
    if kind in {"long", "fill"}:
        return bool(unit.get("fields"))
    return False


def assess_questions(extracted: dict) -> dict:
    units = extracted.get("units") or []
    total = len(units)
    filled = sum(bool(unit.get("filled")) for unit in units)
    blocked = []
    page_requirements = unit_requirements({
        "text": extracted.get("page_instructions", extracted.get("page_text")) or "",
        "file_inputs": extracted.get("file_inputs") or 0,
        "required_file_inputs": extracted.get("required_file_inputs", max(0, (extracted.get("file_inputs") or 0) - (extracted.get("optional_file_inputs") or 0))),
        "optional_file_inputs": extracted.get("optional_file_inputs") or 0,
        "fields": [True] if units else [],
    })
    if page_requirements or not total:
        blocked.append({
            "unit_id": "page", "question_no": None,
            "requirements": page_requirements,
            "reason": "需准备" + "、".join(page_requirements) if page_requirements else "未识别到具体题目和作答控件",
        })
    for unit in units:
        required = unit_requirements(unit)
        issue = _question_issue(unit)
        if not issue and not _controls_complete(unit):
            issue = "题目缺少完整的浏览器作答控件"
        if required or issue:
            blocked.append({
                "unit_id": unit.get("unit_id"), "question_no": unit.get("question_no"),
                "requirements": required,
                "reason": "需准备" + "、".join(required) if required else issue,
            })
    supported = bool(total and not blocked)
    return {
        "policy_version": READINESS_POLICY_VERSION,
        "checked_at": utc_now(), "question_count": int(extracted.get("question_count") or 0),
        "unit_count": total, "filled_units": filled,
        "answer_state": "unknown" if not total else "empty" if not filled else "filled" if filled == total else "partial",
        "execution_state": "direct_fill" if supported else "manual_required",
        "automation_supported": supported,
        "requirements": list(dict.fromkeys(label for item in blocked for label in item["requirements"])),
        "blocked_units": blocked,
        "reason": blocked[0]["reason"] if blocked else "",
    }


def direct_answerable(assignment: dict) -> bool:
    assessment = assignment.get("assessment") or {}
    if assessment.get("execution_state") == "manual_required" or assessment.get("requirements"):
        return False
    if assessment.get("runtime") == "educoder":
        return bool(assessment.get("execution_state") == "code_experiment" and assessment.get("unit_count"))
    return bool(
        assessment.get("policy_version") == READINESS_POLICY_VERSION
        and assessment.get("execution_state") == "direct_fill"
        and assessment.get("automation_supported") is True
    )


def execution_block(assignment: dict) -> str:
    assessment = assignment.get("assessment") or {}
    if assessment.get("execution_state") != "manual_required":
        return ""
    requirements = assessment.get("requirements") or []
    if requirements:
        return "该作业需准备" + "、".join(requirements) + "，请在原页面完成材料后提交"
    reason = assessment.get("reason") or next((item.get("reason") for item in assessment.get("blocked_units", []) if item.get("reason")), "")
    return (reason or "无法确认该作业能在浏览器直接完成") + "，请在原页面核对"
