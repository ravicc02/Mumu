from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath


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
    has_markers = any(re.search(r"\bBegin\b", line, re.I) and re.search(r"/\*|//|^\s*#|^\s*--|<!--", line) for line in lines)
    if not has_markers: return None
    for line in lines:
        begin = bool(re.search(r"\bBegin\b", line, re.I) and re.search(r"/\*|//|^\s*#|^\s*--|<!--", line))
        end = bool(re.search(r"\bEnd\b", line, re.I) and re.search(r"\*/|//|^\s*#|^\s*--|-->", line))
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


def validate_notebook(old, new):
    try:
        before, after = json.loads(old), json.loads(new)
        if not isinstance(before, dict) or not isinstance(after, dict) or before.get("nbformat") != 4 or after.get("nbformat") != 4:
            raise ValueError("Notebook v4 required")
        left, right = before.get("cells"), after.get("cells")
        if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
            raise ValueError("cell structure changed")
        for original, changed in zip(left, right):
            if not isinstance(original, dict) or not isinstance(changed, dict):
                raise ValueError("invalid cell")
            if original.get("cell_type") == "code" and original.get("metadata", {}).get("editable") is not False:
                source = original.get("source")
                replacement = changed.get("source")
                def source_text(value):
                    if isinstance(value, str): return value
                    if isinstance(value, list) and all(isinstance(item, str) for item in value): return "".join(value)
                    raise ValueError("invalid source")
                protected = protected_segments(source_text(source))
                if protected is not None and protected_segments(source_text(replacement)) != protected:
                    raise ValueError("protected source changed")
                source_text(replacement)
                changed["source"] = source
            if original != changed:
                raise ValueError("cell metadata, output or non-code cell changed")
        after["cells"] = left
        if before != after: raise ValueError("notebook metadata changed")
    except (ValueError, TypeError, AttributeError, KeyError) as error:
        raise CodeContractError("Notebook 只能修改可编辑代码单元的 source；单元顺序、说明、元数据及已有输出必须保留。") from error


def validate_patch(files, changes):
    paths = [item.path for item in changes.files]
    if len(set(paths)) != len(paths) or any(path not in files for path in paths):
        raise CodeContractError("模型返回了重复或未授权的文件，未执行写入。")
    result = dict(files)
    for item in changes.files:
        old = files[item.path]
        if PurePosixPath(item.path).suffix.lower() == ".ipynb":
            validate_notebook(old, item.content)
            result[item.path] = item.content
            continue
        protected = protected_segments(old)
        if protected is not None and protected_segments(item.content) != protected:
            raise CodeContractError("模型修改了 Begin/End 之外的脚手架，未执行写入。",
                details={"path": item.path, "rule": "protected_segments"})
        if protected is None and PurePosixPath(item.path).suffix.lower() == ".java":
            expected, actual = java_declarations(old), java_declarations(item.content)
            if expected != actual:
                raise CodeContractError("模型修改了包名、导入或类型声明，未执行写入。",
                    details={"path": item.path, "rule": "java_declarations", "expected": expected, "actual": actual})
        result[item.path] = item.content
    return result
