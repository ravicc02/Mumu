from __future__ import annotations

import re

from ...db import utc_now


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
    if unit.get("file_inputs") and not required: required.append("上传文件")
    if re.search(r"(?:绘制|画出|提交|上传).{0,25}(?:状态机图|活动图|顺序图|用例图|类图|流程图|ER图)", text, re.I):
        required.append("图形或设计文件")
    if "实验或实践报告" in required and "指定文件" in required: required.remove("指定文件")
    return required


def assess_questions(extracted: dict) -> dict:
    units = extracted.get("units") or []
    total = len(units)
    filled = sum(bool(unit.get("filled")) for unit in units)
    blocked = []
    for unit in units:
        required = unit_requirements(unit)
        no_controls = (unit.get("kind") in {"long", "fill"} and "fields" in unit and not unit["fields"]) or (unit.get("kind") in {"single", "multiple"} and "options" in unit and not unit["options"])
        if required or no_controls or unit.get("kind") == "unknown":
            blocked.append({"unit_id": unit.get("unit_id"), "question_no": unit.get("question_no"),
                "requirements": required, "reason": "需准备" + "、".join(required) if required else "未识别到支持的作答控件"})
    return {"checked_at": utc_now(), "question_count": int(extracted.get("question_count") or 0),
        "unit_count": total, "filled_units": filled,
        "answer_state": "unknown" if not total else "empty" if not filled else "filled" if filled == total else "partial",
        "execution_state": "manual_required" if blocked else "direct_fill" if total else "unchecked",
        "requirements": list(dict.fromkeys(label for item in blocked for label in item["requirements"])),
        "blocked_units": blocked}


def execution_block(assignment: dict) -> str:
    assessment = assignment.get("assessment") or {}
    if assessment.get("execution_state") != "manual_required": return ""
    requirements = assessment.get("requirements") or []
    return "该作业需准备" + "、".join(requirements) + "，请在原页面完成材料后提交" if requirements else "该作业没有支持的直接填写控件，请在原页面处理"
