from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class EvaluationReport:
    status: Literal["passed", "failed", "pending", "unknown"]
    kind: str = "unknown"
    summary: str = ""
    details: str = ""
    evidence: dict = field(default_factory=dict)

    @property
    def repairable(self): return self.status == "failed" and self.kind in {"compile", "runtime", "test"}

    @property
    def fingerprint(self): return hashlib.sha256((self.kind + "\n" + self.details).encode()).hexdigest()

    def as_dict(self):
        return {"status":self.status, "kind":self.kind, "summary":self.summary, "details":self.details, "evidence":self.evidence}


def failed_report(text, *, evidence=None):
    text = str(text)[:32000]
    lower = text.lower()
    if any(term in lower for term in ("package org.junit does not exist", "no such file", "permission denied", "imagepullbackoff", "容器启动失败", "服务中断", "资源紧张")):
        kind, summary = "environment", "运行环境或依赖缺失，已保留代码，请检查环境。"
        if any(term in lower for term in ("服务中断", "资源紧张")):
            summary = "头歌评测服务暂不可用，代码已保存，请稍后重试。"
    elif any(term in lower for term in ("compil", "javac", "cannot find symbol", "syntax", "编译")):
        kind, summary = "compile", "代码编译失败，正在根据真实错误修复。"
    elif re.search(r'Exception in thread|java\.(?:lang|util|io)\.\w*Exception|运行(?:时)?异常', text, re.I):
        kind, summary = 'runtime', '代码运行异常，正在根据真实错误修复。'
    else:
        kind, summary = "test", "测试未通过，正在根据平台反馈修复。"
    return EvaluationReport("failed", kind, summary, text, evidence or {})


def classify_evaluation(payload):
    if not isinstance(payload, dict) or type(payload.get("status")) is not int:
        return EvaluationReport("unknown", summary="评测响应结构无法核实")
    status = payload["status"]
    evidence = {key: payload[key] for key in ("status", "game_report_id", "test_sets_count", "sets_error_count") if key in payload}
    if status < 0: return EvaluationReport("pending", "queue", "平台仍在排队或运行", evidence=evidence)
    if status == 2: return EvaluationReport("passed", "platform_receipt", "平台已确认当前评测通过", evidence=evidence)
    if status not in {0, 1}: return EvaluationReport("unknown", summary="平台返回了未知的评测状态", evidence=evidence)
    compiler = str(payload.get("last_compile_output") or "")
    cases = payload.get("test_sets") if isinstance(payload.get("test_sets"), list) else []
    public_cases = []
    compile_failure = False
    for item in cases:
        if not isinstance(item, dict): continue
        if item.get("compile_success") is False or item.get("compile_success") == 0: compile_failure = True
        record = {key: item[key] for key in ("id", "result", "compile_success") if key in item}
        if item.get("actual_output_visible") is True and not item.get("is_invisible"):
            record["actual_output"] = item.get("actual_output")
        public_cases.append(record)
    if not compiler and not public_cases:
        return EvaluationReport("failed", "missing_feedback", "平台判定未通过，但未返回可见的编译或测试信息。", evidence=evidence)
    details = json.dumps({"compiler": compiler, "cases": public_cases}, ensure_ascii=False)[:32000]
    report = failed_report(compiler or details, evidence=evidence)
    if report.kind != "environment":
        compile_failure = compile_failure or any(word in compiler.lower() for word in ("error:", "compilation", "cannot find symbol", "编译失败"))
        report.kind = 'compile' if compile_failure else ('runtime' if report.kind == 'runtime' else 'test')
        report.summary = '代码编译失败，正在根据真实错误修复。' if compile_failure else ('代码运行异常，正在根据真实错误修复。' if report.kind == 'runtime' else '测试未通过，正在根据平台反馈修复。')
    report.details = details
    return report
