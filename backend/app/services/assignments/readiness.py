from __future__ import annotations

import re

from ...db import utc_now


AUTOMATABLE_KINDS = {"single", "multiple", "fill"}


def unit_requirements(unit: dict) -> list[str]:
    text = str(unit.get("text") or "")
    demanded = bool(re.search(r"(?:提交|上传|上交|交付).{0,160}(?:材料|报告|视频|源程序|源码|代码|压缩包|文件|附件|\.(?:docx?|xlsx?|pptx?|pdf))|撰写.{0,50}报告|按.{0,30}(?:附件|模板)|附件.{0,40}要求", text, re.I))
    required = []
    if demanded:
        for label, pattern in (
            ("实验或实践报告", r"(?:实验|实践|实训|课程设计).{0,10}报告|报告.{0,20}(?:模板|\.docx?)"),
            ("项目源码", r"源程序|项目源码|源码.{0,10}(?:压缩包|文件)|程序代码"),
            ("演示视频", r"演示视频|(?:录制|拍摄).{0,15}视频|\.mp4\b"),
            ("压缩包", r"(?:提交|上传|上交).{0,60}压缩包|\.(?:rar|zip|7z)\b"),
            ("指定文件", r"(?:提交|上传|上交).{0,80}\.(?:docx?|xlsx?|pptx?|pdf)\b"),
        ):
            if re.search(pattern, text, re.I): required.append(label)
    if unit.get("file_inputs") and "上传文件" not in required:
        required.append("上传文件")
    if re.search(r"(?:绘制|画出|提交|上传).{0,25}(?:状态机图|活动图|顺序图|用例图|类图|流程图|ER图)", text, re.I):
        required.append("图形或设计文件")
    if "实验或实践报告" in required and "指定文件" in required: required.remove("指定文件")
    return required


def assess_questions(extracted: dict) -> dict:
    units = extracted.get("units") or []
    total = len(units)
    filled = sum(bool(unit.get("filled")) for unit in units)
    blocked = []
    unsupported_types = []
    page_requirements = unit_requirements({
        "kind": "page",
        "text": extracted.get("page_text") or "",
        "file_inputs": extracted.get("file_inputs") or 0,
    })
    if page_requirements:
        blocked.append({
            "unit_id": "page",
            "question_no": None,
            "requirements": page_requirements,
            "kind": "page",
            "reason": "需准备" + "、".join(page_requirements),
        })
    for unit in units:
        required = unit_requirements(unit)
        no_controls = (unit.get("kind") in {"long", "fill"} and "fields" in unit and not unit["fields"]) or (unit.get("kind") in {"single", "multiple"} and "options" in unit and not unit["options"])
        kind = str(unit.get("kind") or "")
        unsupported = kind not in AUTOMATABLE_KINDS
        if unsupported:
            unsupported_types.append(str(unit.get("type") or kind or "未知题型"))
        if required or no_controls or kind == "unknown":
            reason = (
                "需准备" + "、".join(required)
                if required else
                "未识别到支持的作答控件"
            )
            blocked.append({
                "unit_id": unit.get("unit_id"),
                "question_no": unit.get("question_no"),
                "requirements": required,
                "kind": kind,
                "reason": reason,
            })
    return {"checked_at": utc_now(), "question_count": int(extracted.get("question_count") or 0),
        "unit_count": total, "filled_units": filled,
        "answer_state": "unknown" if not total else "empty" if not filled else "filled" if filled == total else "partial",
        "execution_state": "manual_required" if blocked else "direct_fill" if total else "unchecked",
        "automation_supported": bool(total and not blocked and not unsupported_types),
        "supported_kinds": sorted(AUTOMATABLE_KINDS),
        "unsupported_types": list(dict.fromkeys(unsupported_types)),
        "requirements": list(dict.fromkeys(label for item in blocked for label in item["requirements"])),
        "blocked_units": blocked}


def direct_answerable(assignment: dict) -> bool:
    assessment = assignment.get("assessment") or {}
    if assessment.get("runtime") == "educoder":
        return True
    supported = assessment.get("automation_supported")
    if supported is None:
        supported = not assessment.get("unsupported_types")
    return bool(assessment.get("execution_state") == "direct_fill" and supported)


def execution_block(assignment: dict) -> str:
    assessment = assignment.get("assessment") or {}
    if assessment.get("execution_state") != "manual_required": return ""
    requirements = assessment.get("requirements") or []
    if requirements:
        return "该作业需准备" + "、".join(requirements) + "，请在原页面完成材料后提交"
    unsupported = assessment.get("unsupported_types") or []
    if unsupported:
        return "该作业包含" + "、".join(unsupported[:3]) + "，自动处理仅支持选择题和填空题"
    return "该作业没有支持的直接填写控件，请在原页面处理"
