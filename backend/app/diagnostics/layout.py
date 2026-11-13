from __future__ import annotations

import json
import re

FORMAT_VERSION = 2
IMPORTANT_FIELDS = ("message", "trace_id", "task_kind", "phase", "state", "sequence",
                    "elapsed_ms", "data", "stack", "source", "process_session",
                    "source_event_id", "event_id", "timestamp", "local_time", "local_date")
NOISE_CODES = {"assignment.heartbeat", "learning.heartbeat", "browser.frame", "browser.screencast_frame"}


def useful_record(value):
    level = str(value.get("level", "info")).lower()
    if level in {"warning", "warn", "error", "critical", "fatal"} or value.get("stack"):
        return True
    if value.get("code") in NOISE_CODES:
        return False
    component = str(value.get("component", ""))
    message = str(value.get("message", ""))
    data = value.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("http_status"), int) and data["http_status"] >= 400:
        return True
    if component in {"uvicorn.access", "answer-studio.api", ""} or value.get("code") == "legacy.text":
        # Only successful read traffic is noise. Mutations and every HTTP failure remain.
        if re.search(r'(?:HTTP )?(?:GET|HEAD|OPTIONS) [^\r\n]*?(?:HTTP/[\d.]+"? | -> |-> )(?:[23]\d\d)\b', message):
            return False
    if component in {"uvicorn.error", "websockets.server"} and level == "info":
        if message in {"connection open", "connection closed"} or re.search(r'WebSocket .* \[accepted\]$', message):
            return False
    return True


def render_record(value):
    """Readable event blocks with a lossless JSON body for maintenance tooling."""
    value = dict(value)
    value.setdefault("code", "runtime.log")
    level = str(value.get("level", "info")).upper()
    component = value.get("component") or value.get("task_kind") or "application"
    when = str(value.get("local_time") or value.get("timestamp") or "unknown-time")
    heading = "[" + when + "] " + level + " | " + str(component) + " | " + str(value["code"])
    heading = heading.replace("\r", " ").replace("\n", " ")
    fields = dict.fromkeys((*IMPORTANT_FIELDS, *value))
    body = {key: value[key] for key in fields if key in value and key not in {"level", "component", "code"}
            and value[key] is not None and value[key] != "" and value[key] != {} and value[key] != []}
    # Keep the event self-contained; a copied block can still be parsed on its own.
    body = {"level": level.lower(), "component": component, "code": value["code"], **body}
    if isinstance(body.get("stack"), str):
        body["stack"] = body["stack"].splitlines(keepends=True)
    return heading + "\n" + json.dumps(body, ensure_ascii=False, allow_nan=False, indent=2, default=str) + "\n\n"


def iter_records(lines, *, preserve_invalid=False):
    """Read new event blocks and existing JSONL, including a bounded tail view."""
    block = []
    def decode(raw):
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("Log record must be an object")
            if isinstance(value.get("stack"), list):
                value["stack"] = "".join(value["stack"])
            return value
        except (ValueError, TypeError):
            if preserve_invalid:
                return {"code": "log.unparsed", "level": "warning", "message": raw.rstrip()}
            return None
    for line in lines:
        if line.startswith("{"):
            if block:
                value = decode("".join(block))
                if value is not None:
                    yield value
                block = []
            if line.rstrip().endswith("}"):
                value = decode(line)
                if value is not None:
                    yield value
            else:
                block = [line]
        elif block:
            block.append(line)
            if line.rstrip() == "}":
                value = decode("".join(block))
                if value is not None:
                    yield value
                block = []
        elif preserve_invalid and line.strip() and not line.startswith("["):
            value = decode(line)
            if value is not None:
                yield value
    if block and preserve_invalid:
        yield decode("".join(block))
