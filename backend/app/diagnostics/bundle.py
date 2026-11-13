from __future__ import annotations

import hashlib
import html
import io
import json
import sqlite3
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .privacy import Scrubber, task_scrubber
from .layout import iter_records
from .recorder import LOCK, SCHEMA_VERSION, encoded

MAX_BUNDLE_BYTES = 40 * 1024 * 1024


def fixture(nodes, include_content):
    def render(node):
        if not isinstance(node, dict): return ""
        tag = node.get("tag", "")
        if tag == "#text": return html.escape(str(node.get("text", ""))) if include_content else "[content omitted]"
        if tag not in {"div", "article", "section", "span", "p", "h1", "h2", "h3", "h4", "label", "input", "textarea", "button", "select", "option", "ul", "ol", "li", "table", "tbody", "tr", "td", "th", "a", "iframe", "body", "strong", "b", "br"}: tag = "div"
        attrs = []
        for key, value in node.get("attributes", {}).items():
            if key.startswith("on") or key in {"src", "href", "style", "action", "formaction"}: continue
            if isinstance(value, (str, int)): attrs.append(f'{html.escape(key)}="{html.escape(str(value), quote=True)}"')
        if tag in {"button", "input", "select", "textarea"}: attrs.append('disabled="disabled"')
        if include_content and isinstance(node.get("value"), str) and tag == "input": attrs.append(f'value="{html.escape(node["value"], quote=True)}"')
        inner = html.escape(str(node["type_label"])) if node.get("type_label") else ''.join(render(child) for child in node.get("children", []))
        if tag == "textarea" and include_content: inner = html.escape(str(node.get("value", "")))
        return f'<{tag} {" ".join(attrs)}>' + ("" if tag in {"input", "br"} else inner + f'</{tag}>')
    return '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>Offline diagnostic fixture</title><style>body{font:14px sans-serif;padding:20px}iframe{display:none}</style>' + ''.join(render(node) for node in nodes)


def build_bundle(database, kind, identifier, *, include_content=False, include_screenshots=False, renderer=None):
    from ..services.learning.store import LearningStore
    errors = []
    if kind == "assignment":
        task, artifacts = database.get_job(identifier), database.list_artifacts(identifier)
        try: events = database.list_events(identifier)
        except (ValueError, sqlite3.DatabaseError): events = []; errors.append({"file": "ui-events.json", "reason": "database_event_read_failed"})
    elif kind == "learning":
        store = LearningStore(database)
        task, artifacts = store.run(identifier), store.artifacts(identifier)
        try: events = store.events(identifier)
        except (ValueError, sqlite3.DatabaseError): events = []; errors.append({"file": "ui-events.json", "reason": "database_event_read_failed"})
    else: raise ValueError("Unsupported diagnostic task type")
    events = [{key: value for key, value in event.items() if key != "data_json"} for event in events]
    account = database.get_account(task["account_id"])
    scrubber = task_scrubber(database.settings, account)
    trace = database.diagnostics.trace(kind, identifier)
    if not include_content:
        content_sources = [task, events]
        if trace.available:
            with LOCK:
                for path in (trace.folder / "evidence").glob("*.json"):
                    if path.is_symlink() or not path.resolve().is_relative_to(trace.folder.resolve()): continue
                    try:
                        if path.stat().st_size <= 2 * 1024 * 1024: content_sources.append(json.loads(path.read_text(encoding="utf-8")))
                    except (OSError, ValueError): continue
        scrubber.exclude_content_strings(content_sources)
    files, omissions = {}, []
    def add_json(name, value): files[name] = encoded(scrubber.clean(value, include_content=include_content))
    manifest = {"schema_version": SCHEMA_VERSION, "exported_at": datetime.now(timezone.utc).isoformat(), "trace_id": identifier,
        "task_kind": kind, "capture_available": trace.available, "include_content": include_content, "include_screenshots": include_screenshots,
        "task_state_at_export": task["status"], "storage_write_failed": bool(getattr(trace, "failed", False)),
        "renderer_at_export": scrubber.clean(renderer or {}), "omissions": omissions, "read_errors": errors}
    add_json("task.json", task)
    add_json("ui-events.json", events)
    if trace.available:
        with LOCK:
            try: manifest["capture"] = json.loads((trace.folder / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError) as error: errors.append({"file": "capture-manifest", "reason": type(error).__name__})
            lines = []
            for name in ("events.2.jsonl", "events.1.jsonl", "events.jsonl"):
                path = trace.folder / name
                if not path.is_file(): continue
                if path.is_symlink() or not path.resolve().is_relative_to(trace.folder.resolve()) or path.stat().st_size > 3 * 1024 * 1024:
                    errors.append({"file": name, "reason": "journal_path_or_size_rejected"}); continue
                for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict) or record.get("schema_version") != 1 or record.get("trace_id") != identifier:
                            errors.append({"file": name, "line": index + 1, "reason": "invalid_record_identity_or_schema"}); continue
                        lines.append(record)
                    except ValueError: errors.append({"file": name, "line": index + 1, "reason": "invalid_json"})
            files["events.jsonl"] = b"".join(encoded(scrubber.clean(line, include_content=include_content)) + b"\n" for line in lines)
            for path in sorted((trace.folder / "evidence").glob("*.json")):
                if path.is_symlink() or not path.resolve().is_relative_to(trace.folder.resolve()): continue
                try:
                    if path.stat().st_size > 2 * 1024 * 1024:
                        errors.append({"file": path.name, "reason": "evidence_file_size_limit"}); continue
                    item = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(item.get("data"), dict) and item["data"].get("omitted"):
                        omissions.append({"file": path.name, "reason": item["data"]["omitted"]})
                    if item.get("content") and not include_content:
                        omissions.append({"file": "evidence/" + path.name, "reason": "content_not_selected"}); continue
                    add_json("evidence/" + path.name, item)
                    data = item.get("data", {})
                    if isinstance(data, dict) and isinstance(data.get("dom"), list):
                        safe = scrubber.clean(data, include_content=include_content)
                        files["replay/" + path.stem + ".html"] = fixture(safe["dom"], include_content).encode("utf-8")
                except (OSError, ValueError) as error: errors.append({"file": path.name, "reason": type(error).__name__})
    else:
        exists = database.diagnostics.folder(kind, identifier).exists()
        omissions.append({"reason": "diagnostic_storage_unavailable" if exists else "capture_missing_or_expired", "message": "运行现场未采集、已过保留期或存储不完整，没有补造页面或环境。"})
        lines = []
    runtime = deque(maxlen=1000)
    for name in ("backend.jsonl.3", "backend.jsonl.2", "backend.jsonl.1", "backend.jsonl", "oldmain.log", "main.log"):
        path = database.settings.logs_root / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(database.settings.logs_root.resolve()): continue
        try:
            with path.open("rb") as source:
                offset = max(0, path.stat().st_size - 6 * 1024 * 1024)
                if offset:
                    source.seek(offset); source.readline()
                    omissions.append({"file": name, "reason": "runtime_prefix_omitted", "offset": offset})
                for row in iter_records(raw.decode("utf-8", errors="replace") for raw in source):
                    if isinstance(row, dict) and row.get("trace_id") == identifier and row.get("code") in {None, "runtime.log"}: runtime.append(encoded(scrubber.clean(row, include_content=include_content)))
        except (OSError, ValueError) as error: errors.append({"file": name, "reason": type(error).__name__})
    while sum(len(row) for row in runtime) > 2 * 1024 * 1024:
        runtime.popleft()
        if not any(item.get("reason") == "runtime_size_limit" for item in omissions): omissions.append({"reason": "runtime_size_limit"})
    files["runtime.jsonl"] = b"".join(row + b"\n" for row in runtime)
    if not runtime: omissions.append({"reason": "scoped_runtime_records_unavailable"})
    for artifact in artifacts:
        if artifact.get("kind") not in {"screenshot", "live_screenshot", "final_screenshot"}: continue
        path = Path(artifact["path"])
        if not include_screenshots:
            omissions.append({"artifact_id": artifact["id"], "reason": "screenshots_not_selected"}); continue
        if path.is_symlink() or not path.resolve().is_relative_to(database.settings.artifacts_root.resolve()) or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            omissions.append({"artifact_id": artifact["id"], "reason": "artifact_path_rejected"}); continue
        try:
            if path.stat().st_size > 8 * 1024 * 1024: raise ValueError("image_size_limit")
            if sum(len(value) for value in files.values()) + path.stat().st_size > MAX_BUNDLE_BYTES - 1024 * 1024:
                omissions.append({"artifact_id": artifact["id"], "reason": "bundle_size_limit"}); continue
            image = path.read_bytes()
            if not image.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8")): raise ValueError("invalid_image_format")
            files[f"screenshots/{artifact['id']}{path.suffix.lower()}"] = image
        except (OSError, ValueError) as error: errors.append({"artifact_id": artifact["id"], "reason": type(error).__name__})
    problem = next((event for event in reversed(lines) if event.get("code") == "task.exception"), None)
    problem = problem or next((event for event in reversed(lines) if event.get("level") == "error" and "notification" not in event.get("code", "")), None)
    message = (problem.get("message") if problem else task.get("error")) or "未记录到任务异常"
    report = ["# 维护者诊断摘要", "", f"- 格式版本：{SCHEMA_VERSION}", f"- 任务类型：{kind}", f"- 任务编号：{identifier}",
              f"- 状态：{task['status']}", f"- 阶段：{task.get('phase') or 'unknown'}", f"- 诊断采集：{'已启用' if trace.available else '未采集或存储不可用'}", f"- 缺失材料：{len(omissions)} 项；读取异常：{len(errors)} 项", "", "## 首要问题", "", scrubber.text(message), "", "## 定位顺序", "",
              "1. 查看 manifest.json 的应用环境、依赖版本和源码指纹，确认与待修复代码一致。",
              "2. 查看 events.jsonl 的 task.exception、*.failed 和核验事件；runtime.jsonl 只包含此任务关联的运行日志。按 sequence、span_id、unit_id 关联前后步骤。",
              "3. 查看 evidence/ 的页面结构、题型输入、模型结果和核验差异；replay/ 是去除脚本与网络访问的静态片段。",
              "4. 查看 omissions 和 read_errors，缺失的材料不代表执行成功。Cookie、密钥和本地账号标识不随包导出。", "", "## 最近事件", ""]
    for event in events[-30:]: report.append(f"- {event.get('created_at')} {event.get('kind')}: {scrubber.text(event.get('message', ''))[:500]}")
    details = (problem or {}).get("data", {})
    location = (details.get("frames") or [{}])[-1]
    report.extend(["", "## 直接定位", "", f"- 异常类型：{details.get('exception_type') or '查看事件记录'}",
        f"- 调用位置：{scrubber.text(location.get('file', 'unknown'))}:{location.get('line', '?')} {location.get('function', '')}",
        f"- HTTP 状态：{details.get('http_status') or '无'}；上游请求编号：{details.get('request_id') or '无'}"])
    verification = next((event.get("data", {}) for event in reversed(events) if event.get("kind") == "quiz_verification_failed"), {})
    units = verification.get("failures") or ([verification] if verification.get("unit_id") else [])
    for unit in units[:8]: report.append(f"- 作答单元：{unit.get('unit_id', '')} {unit.get('label', '')} {unit.get('type', '')}")
    files["REPORT.md"] = "\n".join(report).encode("utf-8")
    if sum(len(value) for value in files.values()) > MAX_BUNDLE_BYTES: raise ValueError("诊断包超过 40 MB，请取消包含截图后重试")
    manifest["files"] = [{"path": name, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()} for name, value in sorted(files.items())]
    add_json("manifest.json", manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items(): archive.writestr(name, value)
    return output.getvalue()
