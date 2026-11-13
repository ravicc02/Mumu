from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SECRET_KEYS = {"apikey", "accesstoken", "refreshtoken", "token", "password", "passwd", "secret", "authorization", "cookie", "cookies", "setcookie", "storagestate", "localstorage", "sessionstorage", "contexttoken", "bottoken", "enc", "standardenc", "signature", "csrf"}
SECRET_KEYS.update({"pcauthorization", "xedusignature", "seckey", "educodersession"})
CONTENT_KEYS = {"text", "label", "title", "tasktitle", "assignmenttitle", "coursename", "question", "questiontext", "prompt", "messages", "answers", "answer", "values", "fieldvalues", "hiddenvalue", "expected", "thought", "memory", "inputtext", "agentresult", "finalresult", "content", "value", "arialabel", "editortext", "description", "sourcecode"}


def normalize_key(key):
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


class Scrubber:
    def __init__(self, secrets=(), identities=(), paths=()):
        self.replacements = [(str(value), "[REDACTED]") for value in secrets if value and len(str(value)) >= 8]
        self.identities = [str(value) for value in identities if value and len(str(value)) >= 2]
        self.replacements += [(str(value), "[LOCAL_PATH]") for value in paths if value]
        self.replacements.sort(key=lambda item: len(item[0]), reverse=True)
        self.content_replacements = []

    def exclude_content_strings(self, values):
        found = set()
        def visit(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if normalize_key(key) in CONTENT_KEYS:
                        strings = [item] if isinstance(item, str) else item if isinstance(item, list) else []
                        for text in strings:
                            if isinstance(text, str) and len(text.strip()) >= 4:
                                found.add(text); found.add(' '.join(text.split()))
                    visit(item)
            elif isinstance(value, list):
                for item in value: visit(item)
        for value in values: visit(value)
        self.content_replacements = sorted(found, key=len, reverse=True)

    def text(self, value, *, mask_identity=True, mask_content=True):
        value = str(value)
        value = re.sub(r"(?im)^([ \t]*(?:-[ \t]*)?(?:pc-authorization|x-edu-signature|sec[_-]key)[ \t]*:[ \t]*).*$",r"\1[REDACTED]",value)
        for original, replacement in self.replacements: value = value.replace(original, replacement)
        if mask_identity:
            for identity in self.identities: value = re.sub(r"(?<!\w)" + re.escape(identity) + r"(?!\w)", "[ACCOUNT]", value)
        if mask_content:
            for content in self.content_replacements: value = value.replace(content, "[CONTENT]")
        value = re.sub(r"(?:https?|wss?)://[^\s<>\"']+", lambda match: self.url(match[0]), value)
        if "?" in value:
            value = re.sub(r"(?<![\w:])/[^\s<>\"'?]*\?[^\s<>\"']+",
                lambda match: self.url("https://local.invalid" + match[0]).removeprefix("https://local.invalid"), value)
        value = re.sub(r"\bsk-[a-zA-Z0-9_-]{12,}\b|\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", "[REDACTED]", value)
        value = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", value)
        value = re.sub(r"(?im)[\"']?\b(cookie|set-cookie|authorization)[\"']?\s*:\s*[^\r\n]+", r"\1: [REDACTED]", value)
        value = re.sub(r"(?i)\b(api[_-]?key|access[_-]?token|context[_-]?token|password|secret)\s*[=:]\s*[^\s,;]+", r"\1=[REDACTED]", value)
        value = re.sub(r"\b1[3-9]\d{9}\b|\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b", "[PERSONAL_DATA]", value)
        value = re.sub(r"(?i)[a-z]:[\\/]Users[\\/][^\\/\s]+", "[USER_HOME]", value)
        value = re.sub(r"/(?:home|Users)/[^/\s]+", "[USER_HOME]", value)
        return value

    @staticmethod
    def url(value):
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https", "ws", "wss"}: return "[URL]"
            host = parsed.hostname or ""
            if parsed.port: host += f":{parsed.port}"
            query = urlencode([(key, "[REDACTED]") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)])
            path = "/devtools/[REDACTED]" if parsed.path.startswith("/devtools/") else parsed.path
            return urlunsplit((parsed.scheme, host, path, query, ""))
        except ValueError: return "[URL]"

    def clean(self, value, *, include_content=True, depth=0):
        if depth > 48: return {"omitted": "depth_limit"}
        if isinstance(value, dict):
            output = {}
            for key, item in list(value.items())[:150]:
                key = str(key)[:120]
                normalized = normalize_key(key)
                if normalized == "cookies" and isinstance(item, int): output[key] = item
                elif normalized in SECRET_KEYS or normalized.endswith(("apikey", "token", "password", "secret")):
                    output[key] = "[REDACTED]"
                elif normalized in {"accountname", "username", "userid", "cpi", "profiledir", "storagestatepath", "hostname", "machinename"}:
                    output[key] = "[PRIVATE]"
                elif not include_content and normalized in CONTENT_KEYS:
                    raw = json.dumps(item, ensure_ascii=False, default=str)
                    output[key] = {"omitted": "content", "length": len(raw), "sha256": hashlib.sha256(raw.encode()).hexdigest()}
                elif normalized in {"image", "screenshot", "screenshotbase64"}:
                    output[key] = {"omitted": "binary"}
                elif isinstance(item, str) and normalized in {"data", "datajson", "metadata", "payload"} and item.lstrip().startswith(("{", "[")):
                    try:
                        parsed = json.loads(item)
                        output[key] = json.dumps(self.clean(parsed, include_content=include_content, depth=depth + 1), ensure_ascii=False)
                    except ValueError: output[key] = self.text(item)
                elif isinstance(item, str) and (normalized in {"model", "fallbackmodel", "component", "code", "class", "id", "name", "type", "kind", "unitid", "questionid", "chapterid", "resourceid", "function", "typelabel", "state", "phase", "level", "file", "path", "timestamp", "snapshot", "sha256", "buildfingerprint", "os", "osrelease", "python", "architecture", "localtimezone", "reasoningeffort", "appversion", "language", "timezone", "url", "baseurl", "currenturl", "frameurl"} or re.fullmatch(r"[a-fA-F0-9]{32,64}|[a-z][a-z0-9_.:-]{0,160}", item)):
                    output[key] = self.text(item, mask_identity=False, mask_content=False)
                else: output[key] = self.clean(item, include_content=include_content, depth=depth + 1)
            if len(value) > 150: output["_truncated_keys"] = len(value) - 150
            return output
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            output = [self.clean(item, include_content=include_content, depth=depth + 1) for item in values[:500]]
            if len(values) > 500: output.append({"omitted": "list_limit", "count": len(values) - 500})
            return output
        if isinstance(value, (str, Path)):
            result = self.text(value)
            return result if len(result) <= 32000 else result[:32000] + "[TRUNCATED]"
        if isinstance(value, float) and not math.isfinite(value): return {"omitted": "non_finite_number"}
        if value is None or isinstance(value, (bool, int, float)): return value
        return self.text(value)


def task_scrubber(settings, account=None):
    account = account or {}
    secrets = [getattr(settings, key, "") for key in ("openai_api_key", "weixin_bot_token", "weixin_context_token")]
    state = {}
    path = account.get("storage_state_path")
    if path:
        try: state = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError): pass
    secrets.extend(item.get("value", "") for item in state.get("cookies", []))
    for origin in state.get("origins", []): secrets.extend(item.get("value", "") for item in origin.get("localStorage", []))
    scrubber = Scrubber(secrets, [account.get("name"), account.get("username"), getattr(settings, "weixin_user_id", "")],
                        [settings.data_root, Path.home(), Path(__file__).resolve().parents[2]])
    for key in ("openai_api_key", "weixin_bot_token", "weixin_context_token"):
        value = getattr(settings, key, "")
        if value and len(value) < 8: scrubber.replacements.append((value, "[REDACTED]"))
    return scrubber


def auth_summary(account):
    try: state = json.loads(Path(account["storage_state_path"]).read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError): return {"state_available": False}
    cookies = state.get("cookies", [])
    return {"state_available": True, "cookie_count": len(cookies), "cookie_metadata": [
        {key: item.get(key) for key in ("name", "domain", "path", "expires", "secure", "httpOnly", "sameSite")} for item in cookies],
        "origin_hosts": [Scrubber.url(item.get("origin", "")) for item in state.get("origins", [])]}
