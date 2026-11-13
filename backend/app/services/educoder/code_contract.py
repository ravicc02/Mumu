from __future__ import annotations

import hashlib
import json
import re


class CodeContractError(ValueError):
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details or {}


def java_declarations(content):
    # Match declaration text only: \s would also consume preceding blank lines.
    pattern = r"(?m)^[ \t]*(package\s+[^;]+;|import\s+[^;]+;|(?:public\s+)?(?:class|interface|enum)\s+\w+)"
    return re.findall(pattern, content)


def fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def protected_segments(content):
    lines = content.splitlines(keepends=True)
    segments, part, editing = [], [], False
    has_markers = any(re.search(r"\bBegin\b", line, re.I) and re.search(r"/\*|//", line) for line in lines)
    if not has_markers: return None
    for line in lines:
        begin = bool(re.search(r"\bBegin\b", line, re.I) and re.search(r"/\*|//", line))
        end = bool(re.search(r"\bEnd\b", line, re.I) and re.search(r"\*/|//", line))
        if begin:
            if editing: raise CodeContractError("Begin/End 区域嵌套，无法核实编辑边界。")
            part.append(line); segments.append("".join(part)); part = []; editing = True
        elif end:
            if not editing: raise CodeContractError("Begin/End 区域不成对。")
            editing = False; part = [line]
        elif not editing: part.append(line)
    if editing: raise CodeContractError("代码缺少 End 边界。")
    segments.append("".join(part))
    return segments


def validate_patch(files, changes):
    paths = [item.path for item in changes.files]
    if len(set(paths)) != len(paths) or any(path not in files for path in paths):
        raise CodeContractError("模型返回了重复或未授权的文件，未执行写入。")
    result = dict(files)
    for item in changes.files:
        old = files[item.path]
        protected = protected_segments(old)
        if protected is not None and protected_segments(item.content) != protected:
            raise CodeContractError("模型修改了 Begin/End 之外的脚手架，未执行写入。",
                details={"path": item.path, "rule": "protected_segments"})
        if protected is None:
            expected, actual = java_declarations(old), java_declarations(item.content)
            if expected != actual:
                raise CodeContractError("模型修改了包名、导入或类型声明，未执行写入。",
                    details={"path": item.path, "rule": "java_declarations", "expected": expected, "actual": actual})
        result[item.path] = item.content
    return result
