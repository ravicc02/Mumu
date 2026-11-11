from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import os
import platform
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from browser_use import Agent, BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.config import CONFIG
from browser_use.llm.deepseek.chat import ChatDeepSeek
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.tools.service import Tools
import browser_use.utils as browser_use_utils

from ..config import Settings
from ..db import Database, utc_now
from ..events import EventHub
from .notifier import send_completion_notification
from .preview import preview_manager
from .browser import chrome_executable
from .quiz_adapter import (
    QuizAnswerSheet,
    extract_questions,
    fill_answers,
    generate_answers,
)


logger = logging.getLogger(__name__)


_running_jobs: dict[str, asyncio.Task[None]] = {}
_job_controls: dict[str, "JobControl"] = {}
_account_locks: dict[str, asyncio.Lock] = {}
_job_sessions: dict[str, BrowserSession] = {}


@dataclass
class PreservedSession:
    session: BrowserSession
    process: asyncio.subprocess.Process | None
    account_lock: asyncio.Lock


_intervention_sessions: dict[str, PreservedSession] = {}

BROWSER_START_TIMEOUT_SECONDS = 45
BROWSER_STEALTH_SCRIPT = """
(() => {
  try {
    Object.defineProperty(Navigator.prototype, "webdriver", {
      configurable: true,
      get: () => undefined
    });
  } catch (_) {}
})();
"""
CHAOXING_EDITOR_SIMPLIFIER_SCRIPT = """
(() => {
  const install = () => {
    if (document.getElementById("answer-studio-ui-simplifier")) return;
    const style = document.createElement("style");
    style.id = "answer-studio-ui-simplifier";
    style.textContent = `
      .edui-editor-toolbarbox,
      .edui-toolbar,
      .edui-editor-bottomContainer,
      .edui-editor-messageholder,
      .edui-editor-scale,
      .edui-editor-resize {
        display: none !important;
      }
      .edui-editor-iframeholder {
        min-height: 260px !important;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, { once: true });
  } else {
    install();
  }
})();
"""
CONFIG.BROWSER_USE_VERSION_CHECK = False
CONFIG.ANONYMIZED_TELEMETRY = False
CONFIG.BROWSER_USE_CLOUD_SYNC = False

if os.name == "nt":
    platform.system = lambda: "Windows"
    platform.platform = lambda: "Windows"
    platform.machine = lambda: "AMD64"


class _NoopSignalHandler:
    """Avoid browser-use's blocking Windows WMI lookup in embedded runs."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def register(self) -> None:
        pass

    def unregister(self) -> None:
        pass

    def reset(self) -> None:
        pass


browser_use_utils.SignalHandler = _NoopSignalHandler


def _resolve_schema_ref(schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        return {}
    value: Any = schema
    for part in ref[2:].split("/"):
        if not isinstance(value, dict):
            return {}
        value = value.get(part)
    return value if isinstance(value, dict) else {}


def _flattened_action_variants(
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    definitions = parameters.get("$defs") or {}
    union = definitions.get("ActionModelUnion") or {}
    union_items = union.get("anyOf") or []
    if not union_items:
        action_schema = (parameters.get("properties") or {}).get("action") or {}
        union_items = (action_schema.get("items") or {}).get("anyOf") or []
    variants = []
    for item in union_items:
        branch = _resolve_schema_ref(parameters, item.get("$ref", ""))
        if not branch:
            branch = item
        properties = branch.get("properties") or {}
        for action_name, action_schema in properties.items():
            params_schema = (
                _resolve_schema_ref(parameters, action_schema.get("$ref", ""))
                if action_schema.get("$ref")
                else action_schema
            )
            variants.append({
                "action": action_name,
                "properties": set((params_schema.get("properties") or {}).keys()),
                "required": set(params_schema.get("required") or []),
            })
    return variants


def _normalize_flattened_action(
    payload: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    if not payload or "action" in payload:
        return payload
    metadata_keys = {
        "thinking",
        "evaluation_previous_goal",
        "memory",
        "next_goal",
        "current_plan_item",
        "plan_update",
    }
    action_payload = {
        key: value for key, value in payload.items()
        if key not in metadata_keys
    }
    if not action_payload:
        return payload
    keys = set(action_payload)
    candidates = [
        variant
        for variant in _flattened_action_variants(parameters)
        if keys.issubset(variant["properties"])
        and variant["required"].issubset(keys)
    ]
    if not candidates:
        return payload

    preference = {
        "extract": 30,
        "find_elements": 28,
        "search_page": 26,
        "click": 24,
        "input": 22,
        "scroll": 20,
        "evaluate": 18,
        "wait": 16,
    }
    candidates.sort(
        key=lambda item: (
            len(item["required"]),
            preference.get(item["action"], 0),
            -len(item["properties"] - keys),
        ),
        reverse=True,
    )
    selected = candidates[0]
    normalized: dict[str, Any] = {}
    required = set(parameters.get("required") or [])
    for key in metadata_keys:
        if key in payload:
            normalized[key] = payload[key]
    normalized.setdefault("memory", "正在执行页面动作。")
    if "evaluation_previous_goal" in required:
        normalized.setdefault("evaluation_previous_goal", "继续执行当前作业任务。")
    if "next_goal" in required:
        normalized.setdefault("next_goal", "完成当前页面动作。")
    normalized["action"] = [{selected["action"]: action_payload}]
    return normalized


def _normalize_tool_response(
    response: Any,
    parameters: dict[str, Any] | None,
) -> Any:
    if not parameters:
        return response
    choices = getattr(response, "choices", None) or []
    if not choices:
        return response
    message = getattr(choices[0], "message", None)
    tool_calls = getattr(message, "tool_calls", None) or []
    for tool_call in tool_calls:
        function = getattr(tool_call, "function", None)
        arguments = getattr(function, "arguments", None)
        try:
            payload = (
                json.loads(arguments)
                if isinstance(arguments, str)
                else arguments
            )
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        normalized = _normalize_flattened_action(payload, parameters)
        if normalized == payload:
            continue
        encoded = json.dumps(normalized, ensure_ascii=False)
        if isinstance(arguments, str):
            function.arguments = encoded
        else:
            function.arguments = normalized
        logger.info(
            "Normalized flattened DeepSeek action into AgentOutput: %s",
            normalized["action"][0].keys(),
        )
    return response


class _ReasoningCompletions:
    def __init__(self, delegate: Any, reasoning_effort: str | None) -> None:
        self._delegate = delegate
        self._reasoning_effort = reasoning_effort

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        if self._reasoning_effort:
            kwargs.setdefault("reasoning_effort", self._reasoning_effort)
        response = await self._delegate.create(*args, **kwargs)
        tools = kwargs.get("tools") or []
        parameters = (
            tools[0].get("function", {}).get("parameters")
            if tools and isinstance(tools[0], dict)
            else None
        )
        return _normalize_tool_response(response, parameters)


class _ReasoningChat:
    def __init__(self, delegate: Any, reasoning_effort: str | None) -> None:
        self.completions = _ReasoningCompletions(
            delegate.completions,
            reasoning_effort,
        )


class _ReasoningClient:
    def __init__(self, delegate: Any, reasoning_effort: str | None) -> None:
        self.chat = _ReasoningChat(delegate.chat, reasoning_effort)


@dataclass
class ReasoningDeepSeekChat(ChatDeepSeek):
    reasoning_effort: str | None = None

    def _client(self) -> Any:
        return _ReasoningClient(
            super()._client(),
            self.reasoning_effort,
        )


def _reasoning_effort(settings: Settings) -> str:
    effort = settings.reasoning_effort.strip().lower()
    if effort in {"none", "minimal", "low", "medium", "high", "xhigh"}:
        return effort
    return "medium"


def _build_agent_llm(
    settings: Settings,
    model: str,
    *,
    temperature: float,
    max_retries: int,
) -> Any:
    if "deepseek" in model.lower():
        return ReasoningDeepSeekChat(
            model=model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            temperature=temperature,
            max_tokens=8192,
            timeout=180,
            client_params={"max_retries": max_retries},
            reasoning_effort=_reasoning_effort(settings),
        )
    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=temperature,
        frequency_penalty=0,
        timeout=180,
        max_retries=max_retries,
        max_completion_tokens=8192,
        reasoning_effort=_reasoning_effort(settings),
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_cdp(
    port: int,
    process: asyncio.subprocess.Process,
    timeout: float,
) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    endpoint = f"http://127.0.0.1:{port}/json/version"
    async with httpx.AsyncClient(trust_env=False, timeout=1.5) as client:
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                raise RuntimeError(
                    f"浏览器启动失败，退出码 {process.returncode}"
                )
            try:
                response = await client.get(endpoint)
                if response.status_code == 200:
                    websocket_url = response.json().get("webSocketDebuggerUrl")
                    if websocket_url:
                        return str(websocket_url)
            except Exception:
                pass
            await asyncio.sleep(0.2)
    raise TimeoutError("浏览器调试端口未在限定时间内就绪")


async def _launch_chrome(
    profile_dir: str,
    headless: bool,
    timeout: float,
) -> tuple[asyncio.subprocess.Process, str]:
    port = _free_port()
    args = [
        str(chrome_executable()),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-search-engine-choice-screen",
        "--disable-background-networking",
        "--disable-component-update",
        "--remote-allow-origins=*",
    ]
    if headless:
        args.extend([
            "--headless=new",
            "--window-size=1440,1000",
        ])
    else:
        args.extend([
            "--start-maximized",
            "--window-size=1440,1000",
        ])
    args.append("about:blank")
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        cdp_url = await _wait_for_cdp(port, process, timeout)
    except Exception:
        await _terminate_process(process)
        raise
    return process, cdp_url


async def _terminate_process(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _capture_screenshot(
    session: BrowserSession,
    full_page: bool = False,
) -> tuple[bytes, str]:
    page = await session.get_current_page()
    if page is None:
        raise RuntimeError("没有可截图的浏览器页面")
    params: dict[str, Any] = {"format": "png", "fromSurface": True}
    if full_page:
        params["captureBeyondViewport"] = True
    session_id = await _resolve_session_id(page)
    result = await session.cdp_client.send.Page.captureScreenshot(
        params,
        session_id=session_id,
    )
    return base64.b64decode(result["data"]), await session.get_current_page_url()


async def _resolve_session_id(page: Any) -> str:
    value = page.session_id
    if inspect.isawaitable(value):
        value = await value
    return str(value)


async def _restore_storage_state_to_session(
    session: BrowserSession,
    storage_state_path: str,
) -> int:
    path = Path(storage_state_path)
    if not path.is_file():
        raise RuntimeError("账号登录状态文件不存在，请刷新 Cookie")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("账号登录状态文件无法读取，请刷新 Cookie") from error

    cookies = []
    for raw_cookie in state.get("cookies") or []:
        if not isinstance(raw_cookie, dict):
            continue
        cookie = {
            key: raw_cookie[key]
            for key in (
                "name",
                "value",
                "domain",
                "path",
                "httpOnly",
                "secure",
                "sameSite",
            )
            if key in raw_cookie
        }
        expires = raw_cookie.get("expires")
        if expires not in (None, 0, 0.0, -1, -1.0):
            cookie["expires"] = expires
        if cookie.get("name") and cookie.get("domain"):
            cookies.append(cookie)

    if not cookies:
        raise RuntimeError("账号登录状态中没有 Cookie，请重新登录授权")

    await session.get_current_page()
    setter = getattr(session, "_cdp_set_cookies", None)
    if setter is None:
        raise RuntimeError("当前浏览器通道不支持恢复登录 Cookie")
    await setter(cookies)

    init_script_adder = getattr(session, "_cdp_add_init_script", None)
    if init_script_adder is not None:
        for origin in state.get("origins") or []:
            origin_value = origin.get("origin")
            if not origin_value:
                continue
            statements = [
                f"window.localStorage.setItem({json.dumps(item['name'])}, {json.dumps(item['value'])});"
                for item in origin.get("localStorage") or []
                if item.get("name") is not None
            ]
            session_statements = [
                f"window.sessionStorage.setItem({json.dumps(item['name'])}, {json.dumps(item['value'])});"
                for item in origin.get("sessionStorage") or []
                if item.get("name") is not None
            ]
            script = (
                "(function(){"
                f"if(window.location.origin!=={json.dumps(origin_value)})return;"
                "try{"
                + "".join(statements)
                + "".join(session_statements)
                + "}catch(e){}"
                "})();"
            )
            await init_script_adder(script)
    return len(cookies)


async def _install_page_simplifier(session: BrowserSession) -> None:
    adder = getattr(session, "_cdp_add_init_script", None)
    if adder is not None:
        await adder(BROWSER_STEALTH_SCRIPT)
        await adder(CHAOXING_EDITOR_SIMPLIFIER_SCRIPT)


async def _verify_session_page(
    session: BrowserSession,
    verification_url: str,
    timeout: float = 12,
) -> str:
    page = await session.get_current_page()
    if page is None:
        raise RuntimeError("Agent 浏览器没有可用页面")
    session_id = await _resolve_session_id(page)
    await session.cdp_client.send.Page.navigate(
        params={"url": verification_url},
        session_id=session_id,
    )
    expected_host = (urlparse(verification_url).hostname or "").lower()
    deadline = asyncio.get_running_loop().time() + timeout
    last_url = ""
    while asyncio.get_running_loop().time() < deadline:
        try:
            last_url = await session.get_current_page_url()
        except Exception:
            last_url = ""
        lowered = last_url.lower()
        if "passport2.chaoxing.com" in lowered or "/login" in lowered:
            raise RuntimeError("Agent 浏览器未继承账号登录态，请刷新 Cookie")
        current_host = (urlparse(last_url).hostname or "").lower()
        if current_host and (
            current_host == expected_host
            or current_host.endswith(".chaoxing.com")
        ):
            return last_url
        await asyncio.sleep(0.5)
    raise RuntimeError(
        f"Agent 登录态验证超时（当前页面：{last_url or 'about:blank'}）"
    )


def _account_lock(account_id: str) -> asyncio.Lock:
    lock = _account_locks.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _account_locks[account_id] = lock
    return lock


class JobStopped(Exception):
    pass


class AgentOperationError(RuntimeError):
    def __init__(
        self,
        stage: str,
        reason: str,
        suggestion: str,
        detail: str = "",
    ) -> None:
        super().__init__(reason)
        self.stage = stage
        self.reason = reason
        self.suggestion = suggestion
        self.detail = detail


def _compact_text(value: Any, limit: int = 600) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _diagnose_error(
    error: Exception,
    *,
    stage: str,
    current_url: str,
    last_action: str,
) -> dict[str, str]:
    raw = _compact_text(error, 1800)
    if isinstance(error, AgentOperationError):
        reason = error.reason
        suggestion = error.suggestion
        stage = error.stage
        raw = _compact_text(error.detail or error.reason, 1800)
    elif any(
        marker in raw.lower()
        for marker in (
            "agoutput",
            "json",
            "validation error",
            "function call",
            "tool_call",
            "模型输出",
        )
    ):
        stage = "模型输出"
        reason = "模型返回内容不符合 Agent 工具协议，无法转换成可执行动作。"
        suggestion = "提高思考强度或切换到更强的模型后重试。"
    elif any(
        marker in raw.lower()
        for marker in (
            "登录态",
            "cookie",
            "passport",
            "login",
            "未登录",
        )
    ):
        stage = "登录态"
        reason = "账号 Cookie 或登录态失效，浏览器无法进入已授权页面。"
        suggestion = "在账号右侧菜单刷新 Cookie，确认登录成功后重试。"
    elif any(
        marker in raw.lower()
        for marker in (
            "javascript evaluation",
            "evaluate",
            "执行脚本",
            "页面脚本",
        )
    ):
        stage = "页面脚本"
        reason = "页面脚本执行失败，可能是作业页 DOM 已更新或脚本上下文失效。"
        suggestion = "重新执行任务；若持续失败，进入实时浏览器检查作业页是否正常加载。"
    elif any(
        marker in raw.lower()
        for marker in (
            "cdp",
            "ax_tree",
            "screenshot",
            "截图",
            "页面读取超时",
            "dom build",
        )
    ):
        stage = "浏览器页面"
        reason = "浏览器页面读取或截图超时，Agent 无法继续确认页面状态。"
        suggestion = "关闭其它占用浏览器的任务后重试，或进入实时浏览器检查页面是否卡住。"
    elif any(
        marker in raw.lower()
        for marker in (
            "未通过核验",
            "没有返回全部",
            "作答单元",
            "填空",
            "隐藏值",
        )
    ):
        stage = "答题写入"
        reason = "题目识别后，部分答案没有成功写入或没有通过页面值核验。"
        suggestion = "进入实时浏览器检查失败题号，处理后结束介入，或重新执行任务。"
    elif any(
        marker in raw.lower()
        for marker in ("timeout", "timed out", "超时")
    ):
        stage = "执行超时"
        reason = "任务在限定时间内没有完成。"
        suggestion = "检查网络和模型响应速度，减少同时执行的任务后重试。"
    else:
        reason = _compact_text(raw or "未返回具体错误信息", 500)
        suggestion = "查看执行凭证和 Agent 日志；若问题重复，重新执行任务。"

    summary_parts = [f"{stage}失败：{reason}"]
    if current_url:
        summary_parts.append(f"最后页面：{_compact_text(current_url, 240)}")
    if last_action:
        summary_parts.append(f"最近动作：{_compact_text(last_action, 320)}")
    summary_parts.append(f"建议：{suggestion}")
    return {
        "stage": stage,
        "reason": reason,
        "suggestion": suggestion,
        "current_url": current_url,
        "last_action": last_action,
        "raw_error": raw,
        "summary": " ".join(summary_parts),
    }


def _stage_for_event(kind: str, message: str) -> str:
    if kind in {"browser_starting", "browser_ready", "session_ready", "browser"}:
        return "浏览器启动与登录"
    if kind in {"quiz_navigating", "quiz_extracted"}:
        return "题型识别"
    if kind in {"quiz_answers_ready"}:
        return "模型生成答案"
    if kind in {"quiz_filled"}:
        return "页面填写与核验"
    if kind in {"thinking", "step"}:
        return "Agent 页面执行"
    if kind in {
        "result_summary",
        "diagnostic",
        "needs_intervention",
        "incomplete",
        "completed",
        "stopped",
        "error",
        "final_screenshot",
        "notification",
        "notification_error",
    }:
        return "结果整理"
    return _compact_text(message, 80) or "任务执行"


def _summarize_action_details(details: list[dict[str, Any]]) -> str:
    parts = []
    for item in details:
        action_type = str(item.get("type") or "动作")
        payload = item.get("data") or {}
        nested = next(
            (
                value
                for value in payload.values()
                if isinstance(value, dict)
            ),
            payload,
        ) if isinstance(payload, dict) else {}
        if isinstance(nested, dict) and nested.get("url"):
            parts.append(f"打开 {nested['url']}")
        elif isinstance(nested, dict) and nested.get("text"):
            parts.append(f"输入「{_compact_text(nested['text'], 80)}」")
        elif isinstance(nested, dict) and nested.get("code"):
            parts.append("执行页面脚本")
        elif isinstance(nested, dict) and nested.get("index") is not None:
            parts.append(f"操作元素 #{nested['index']}")
        else:
            parts.append(action_type)
    return "；".join(parts)


class JobControl:
    def __init__(self) -> None:
        self.resume_event = asyncio.Event()
        self.resume_event.set()
        self.stop_event = asyncio.Event()
        self.stop_requested = False

    async def wait_if_paused(self, _agent: Any = None) -> None:
        if self.stop_requested:
            raise JobStopped("用户停止任务")
        await self.resume_event.wait()
        if self.stop_requested:
            raise JobStopped("用户停止任务")


def start_job(
    database: Database,
    event_hub: EventHub,
    settings: Settings,
    job_id: str,
) -> None:
    existing = _running_jobs.get(job_id)
    if existing and not existing.done():
        return
    task = asyncio.create_task(
        run_job(database, event_hub, settings, job_id)
    )
    _running_jobs[job_id] = task


async def focus_job_browser(job_id: str) -> bool:
    session = _job_sessions.get(job_id)
    if session is None:
        raise RuntimeError("任务当前没有可操作的浏览器")
    page = await session.get_current_page()
    if page is None:
        raise RuntimeError("任务当前没有可操作的页面")
    session_id = await _resolve_session_id(page)
    try:
        window = await session.cdp_client.send.Browser.getWindowForTarget()
        window_id = window.get("windowId")
        if window_id is not None:
            await session.cdp_client.send.Browser.setWindowBounds({
                "windowId": window_id,
                "bounds": {"windowState": "normal"},
            })
    except Exception:
        logger.debug("Could not restore browser window before focus", exc_info=True)
    await session.cdp_client.send.Page.bringToFront(
        session_id=session_id,
    )
    return True


async def capture_job_snapshot(job_id: str) -> dict[str, str]:
    session = _job_sessions.get(job_id)
    if session is None:
        raise RuntimeError("任务当前没有可预览的浏览器")
    image, url = await _capture_screenshot(session)
    return {
        "image": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        "url": url,
    }


async def _run_structured_chaoxing_quiz(
    database: Database,
    job: dict[str, Any],
    session: BrowserSession,
    llm: Any,
    emit: Any,
) -> dict[str, Any] | None:
    if "/mooc2/work/task" not in str(job.get("task_url") or ""):
        return None

    await emit("quiz_navigating", "正在打开作业页并读取全部题型")
    await session.navigate_to(job["task_url"])
    await asyncio.sleep(4)
    page = await session.get_current_page()
    if page is None:
        raise RuntimeError("作业页没有可读取的浏览器页面")
    await emit("agent_ready", "结构化答题器已就绪，正在读取全部题型")

    extracted: dict[str, Any] = {}
    for _ in range(4):
        extracted = await extract_questions(page)
        if int(extracted.get("question_count") or 0) > 0:
            break
        await asyncio.sleep(2)
    question_count = int(extracted.get("question_count") or 0)
    unit_count = int(extracted.get("unit_count") or 0)
    if question_count == 0 or unit_count == 0:
        return None

    database.update_job(
        job["id"],
        progress=25,
        last_heartbeat_at=utc_now(),
    )
    await emit(
        "quiz_extracted",
        f"已识别 {question_count} 个大题、{unit_count} 个作答单元",
        {
            "question_count": question_count,
            "unit_count": unit_count,
            "question_types": sorted({
                str(unit.get("type") or "未知")
                for unit in extracted.get("units") or []
            }),
        },
    )

    try:
        answer_sheet = await asyncio.wait_for(
            generate_answers(llm, extracted),
            timeout=300,
        )
    except TimeoutError as error:
        raise AgentOperationError(
            "模型生成答案",
            "模型在 5 分钟内没有返回全部题目的结构化答案。",
            "提高模型响应速度或减少单次题量后重试。",
            str(error),
        ) from error
    expected_ids = {
        str(unit["unit_id"])
        for unit in extracted.get("units") or []
    }
    answer_by_id = {
        answer.unit_id: answer
        for answer in answer_sheet.answers
    }
    missing_ids = expected_ids - set(answer_by_id)
    if missing_ids:
        raise AgentOperationError(
            "模型生成答案",
            "模型没有返回全部作答单元："
            + "、".join(sorted(missing_ids)[:8]),
            "切换到更强的模型或重新执行任务，确保每个题号都有答案。",
        )
    if len(answer_by_id) != len(expected_ids):
        raise AgentOperationError(
            "模型生成答案",
            "模型返回了重复或未知的作答单元，答案与页面题号无法一一对应。",
            "重新执行任务；若持续出现，降低并发并提高模型思考强度。",
        )

    database.update_job(
        job["id"],
        progress=65,
        last_heartbeat_at=utc_now(),
    )
    await emit(
        "quiz_answers_ready",
        f"已生成 {len(answer_sheet.answers)} 组结构化答案",
        {"answer_count": len(answer_sheet.answers)},
    )

    filled = await fill_answers(page, answer_sheet)
    await asyncio.sleep(1)
    filled_by_id = {
        str(item.get("unit_id")): item
        for item in filled
    }
    invalid_ids = []
    for unit in extracted.get("units") or []:
        unit_id = str(unit["unit_id"])
        result = filled_by_id.get(unit_id)
        if result is None:
            invalid_ids.append(unit_id)
            continue
        if unit["kind"] in {"single", "multiple", "shared"}:
            if not result.get("hidden_value"):
                invalid_ids.append(unit_id)
        else:
            field_values = result.get("field_values") or []
            if (
                len(field_values) != len(unit.get("fields") or [])
                or any(not str(value).strip() for value in field_values)
            ):
                invalid_ids.append(unit_id)
    if invalid_ids:
        raise AgentOperationError(
            "页面填写与核验",
            "以下作答单元写入后未通过核验："
            + "、".join(sorted(invalid_ids)[:8]),
            "进入实时浏览器检查这些题号，修正后结束介入或重新执行。",
        )

    database.update_job(
        job["id"],
        progress=95,
        last_heartbeat_at=utc_now(),
    )
    await emit(
        "quiz_filled",
        f"全部 {unit_count} 个作答单元已写入并通过页面值核验",
        {
            "filled_count": len(filled),
            "invalid_count": 0,
        },
    )
    return {
        "question_count": question_count,
        "unit_count": unit_count,
        "filled_count": len(filled),
        "extracted": extracted,
        "answers": answer_sheet.model_dump(mode="json"),
        "filled": filled,
    }


async def finish_job_intervention(
    database: Database,
    event_hub: EventHub,
    job_id: str,
) -> dict[str, Any]:
    job = database.get_job(job_id)
    if job["status"] != "needs_intervention":
        raise RuntimeError("当前任务不在待介入状态")
    preserved = _intervention_sessions.pop(job_id, None)
    if preserved is not None:
        try:
            await asyncio.wait_for(preserved.session.kill(), timeout=8)
        except Exception:
            pass
        await _terminate_process(preserved.process)
        if preserved.account_lock.locked():
            preserved.account_lock.release()
    _job_sessions.pop(job_id, None)
    database.transition_job(
        job_id,
        "stopped",
        error="用户已结束手动介入",
        finished_at=utc_now(),
    )
    event = database.add_event(job_id, "stopped", "已结束手动介入")
    await event_hub.publish(job_id, event)
    return database.get_job(job_id)


async def close_preserved_sessions() -> None:
    for job_id, preserved in list(_intervention_sessions.items()):
        _intervention_sessions.pop(job_id, None)
        try:
            await asyncio.wait_for(preserved.session.kill(), timeout=8)
        except Exception:
            pass
        await _terminate_process(preserved.process)
        if preserved.account_lock.locked():
            preserved.account_lock.release()
        _job_sessions.pop(job_id, None)


def pause_job(
    database: Database,
    event_hub: EventHub,
    job_id: str,
) -> None:
    job = database.get_job(job_id)
    if job["status"] not in {"queued", "running"}:
        raise RuntimeError("只有排队中或运行中的任务可以暂停")
    control = _job_controls.get(job_id)
    if not control:
        raise RuntimeError("任务当前未运行")
    control.resume_event.clear()
    database.transition_job(
        job_id,
        "paused",
        paused_from_status=job["status"],
    )
    event = database.add_event(job_id, "paused", "任务已暂停")
    asyncio.create_task(event_hub.publish(job_id, event))


def resume_job(
    database: Database,
    event_hub: EventHub,
    job_id: str,
) -> None:
    job = database.get_job(job_id)
    if job["status"] != "paused":
        raise RuntimeError("只有已暂停的任务可以继续")
    control = _job_controls.get(job_id)
    if not control:
        raise RuntimeError("任务当前未运行")
    resume_status = job.get("paused_from_status") or "running"
    if resume_status not in {"queued", "running"}:
        resume_status = "running"
    control.resume_event.set()
    database.transition_job(
        job_id,
        resume_status,
        paused_from_status=None,
    )
    event = database.add_event(
        job_id,
        "resumed",
        "任务已继续排队" if resume_status == "queued" else "任务已继续",
    )
    asyncio.create_task(event_hub.publish(job_id, event))


def stop_job(
    database: Database,
    event_hub: EventHub,
    job_id: str,
) -> None:
    job = database.get_job(job_id)
    if job["status"] not in {"queued", "running", "paused"}:
        raise RuntimeError("当前任务不能停止")
    control = _job_controls.get(job_id)
    if not control:
        raise RuntimeError("任务当前未运行")
    control.stop_requested = True
    control.stop_event.set()
    control.resume_event.set()
    database.transition_job(job_id, "stopping")
    event = database.add_event(job_id, "stopping", "正在停止任务")
    asyncio.create_task(event_hub.publish(job_id, event))
    asyncio.create_task(_force_stop_after(job_id))


async def _force_stop_after(job_id: str, timeout: float = 12) -> None:
    task = _running_jobs.get(job_id)
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return
    except TimeoutError:
        task.cancel("任务停止超时，执行强制终止")


async def run_job(
    database: Database,
    event_hub: EventHub,
    settings: Settings,
    job_id: str,
) -> None:
    job = database.get_job(job_id)
    account = database.get_account(job["account_id"])
    artifact_dir = settings.artifacts_root / job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    current_stage = "任务初始化"
    last_known_url = ""
    last_action_summary = ""

    async def emit(
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal current_stage
        if kind not in {"heartbeat", "screenshot", "warning"}:
            current_stage = _stage_for_event(kind, message)
        event = database.add_event(job_id, kind, message, data)
        await event_hub.publish(job_id, event)
        return event

    async def notify_safely(payload: dict[str, Any]) -> None:
        try:
            sent = await send_completion_notification(settings, payload)
            if not any(sent.values()):
                return
            await emit(
                "notification",
                "通知已发送",
                {"channels": sent},
            )
        except Exception as notification_error:
            await emit(
                "notification_error",
                f"通知发送失败：{notification_error}",
            )

    control = JobControl()
    _job_controls[job_id] = control
    session: BrowserSession | None = None
    browser_process: asyncio.subprocess.Process | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    step_counter = 0
    account_lock = _account_lock(job["account_id"])
    lock_acquired = False
    preserve_session = False
    await emit("queued", "任务已创建，等待当前账号的 Agent 空闲")
    try:
        if not settings.openai_api_key:
            raise RuntimeError("未配置 OPENAI_API_KEY")

        await control.wait_if_paused()
        acquire_task = asyncio.create_task(account_lock.acquire())
        stop_task = asyncio.create_task(control.stop_event.wait())
        done, _ = await asyncio.wait(
            {acquire_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done and not acquire_task.done():
            acquire_task.cancel()
            await asyncio.gather(acquire_task, return_exceptions=True)
            raise JobStopped("用户停止排队任务")
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        lock_acquired = True
        await control.wait_if_paused()
        database.transition_job(
            job_id,
            "running",
            started_at=utc_now(),
            progress=1,
            paused_from_status=None,
        )
        await emit("status", "账号浏览器已分配，Agent 任务开始运行")

        interactive = bool(job["interactive"])
        await emit(
            "browser_starting",
            "正在释放预览并启动 Chrome",
            {"interactive": interactive},
        )
        await preview_manager.invalidate(job["account_id"])
        browser_process, cdp_url = await _launch_chrome(
            account["profile_dir"],
            headless=not interactive,
            timeout=BROWSER_START_TIMEOUT_SECONDS,
        )
        await emit(
            "browser_ready",
            "Chrome 已启动，正在连接 Agent 控制通道",
            {"pid": browser_process.pid},
        )
        profile = BrowserProfile(
            user_data_dir=account["profile_dir"],
            storage_state=None,
            headless=not interactive,
            viewport=None if interactive else {"width": 1440, "height": 1000},
            no_viewport=interactive,
            keep_alive=True,
            enable_default_extensions=False,
            cdp_url=cdp_url,
        )
        session = BrowserSession(
            browser_profile=profile,
            cdp_url=cdp_url,
            is_local=False,
        )
        try:
            await asyncio.wait_for(
                session.start(),
                timeout=20,
            )
        except TimeoutError as error:
            raise RuntimeError(
                "Agent 控制通道连接超时，请重试。"
            ) from error
        restored_cookies = await _restore_storage_state_to_session(
            session,
            account["storage_state_path"],
        )
        if account["platform"] == "chaoxing":
            await _install_page_simplifier(session)
        verification_url = (
            "https://i.chaoxing.com/base?ws=1"
            if account["platform"] == "chaoxing"
            else job["task_url"]
        )
        verified_url = await _verify_session_page(
            session,
            verification_url,
        )
        await emit(
            "session_ready",
            "账号登录态已注入并验证",
            {
                "cookies": restored_cookies,
                "url": verified_url,
            },
        )
        await emit(
            "browser",
            "可操控浏览器已连接",
            {"interactive": interactive, "pid": browser_process.pid},
        )
        _job_sessions[job_id] = session

        await emit("agent_starting", "正在初始化模型和 Agent")
        is_deepseek = "deepseek" in settings.openai_model.lower()
        llm = _build_agent_llm(
            settings,
            settings.openai_model,
            temperature=0.1,
            max_retries=1,
        )
        structured_result = await _run_structured_chaoxing_quiz(
            database,
            job,
            session,
            llm,
            emit,
        )
        if structured_result is not None:
            if control.stop_requested:
                raise JobStopped("用户停止结构化答题任务")
            screenshot, current_url = await _capture_screenshot(
                session,
                full_page=True,
            )
            screenshot_path = artifact_dir / "final.png"
            screenshot_path.write_bytes(screenshot)
            screenshot_artifact = database.add_artifact(
                job_id,
                "final_screenshot",
                screenshot_path,
            )
            screenshot_event = database.add_event(
                job_id,
                "final_screenshot",
                "全部题型填写完成，最终页面已保存",
                {
                    "artifact_id": screenshot_artifact["id"],
                    "url": current_url,
                },
            )
            await event_hub.publish(
                job_id,
                {
                    **screenshot_event,
                    "data": {
                        **screenshot_event["data"],
                        "image": (
                            "data:image/png;base64,"
                            + base64.b64encode(screenshot).decode("ascii")
                        ),
                    },
                },
            )
            result_path = artifact_dir / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "job": job,
                        "structured_result": structured_result,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            database.add_artifact(job_id, "result_json", result_path)
            preserve_session = True
            _intervention_sessions[job_id] = PreservedSession(
                session=session,
                process=browser_process,
                account_lock=account_lock,
            )
            intervention_summary = (
                "等待人工介入："
                f"已识别 {structured_result['question_count']} 个大题、"
                f"{structured_result['unit_count']} 个作答单元，"
                f"并成功写入 {structured_result['filled_count']} 个。"
                "原因：当前任务设置为仅填写，需要人工核对页面后决定是否提交。"
                f"最后页面：{_compact_text(current_url, 240)}。"
                "建议：点击“进入实时浏览器”检查重点题目、编辑器内容和答题卡状态。"
            )
            await emit(
                "result_summary",
                intervention_summary,
                {
                    "status": "needs_intervention",
                    "question_count": structured_result["question_count"],
                    "unit_count": structured_result["unit_count"],
                    "filled_count": structured_result["filled_count"],
                    "current_url": current_url,
                },
            )
            database.transition_job(
                job_id,
                "needs_intervention",
                submission_status="filled_pending_review",
                progress=95,
                error=intervention_summary,
            )
            await emit(
                "needs_intervention",
                "全部题型已填写并通过页面值核验，等待人工核对",
                {
                    "question_count": structured_result["question_count"],
                    "unit_count": structured_result["unit_count"],
                    "filled_count": structured_result["filled_count"],
                },
            )
            await notify_safely(
                {
                    "event": "assignment_needs_intervention",
                    "job_id": job_id,
                    "account": account["name"],
                    "course": job["course_name"],
                    "assignment": job["assignment_title"],
                    "auto_submit": bool(job["auto_submit"]),
                    "submission_status": "filled_pending_review",
                    "result": (
                        f"已填写 {structured_result['unit_count']} 个作答单元，"
                        "等待人工核对"
                    ),
                }
            )
            return
        fallback_model = (
            settings.openai_fallback_model.strip()
            or settings.openai_model
        )
        fallback_llm = _build_agent_llm(
            settings,
            fallback_model,
            temperature=0,
            max_retries=2,
        )

        async def on_step(
            _browser_state: Any,
            output: Any,
            step: int,
        ) -> None:
            nonlocal step_counter, last_action_summary, last_known_url
            step_counter = step + 1
            progress = min(95, 5 + step_counter * 3)
            database.update_job(
                job_id,
                progress=progress,
                last_heartbeat_at=utc_now(),
            )
            thought = ""
            action_names: list[str] = []
            action_details: list[dict[str, Any]] = []
            if output is not None:
                if hasattr(output, "current_state"):
                    state = output.current_state
                    thought = str(
                        getattr(state, "next_goal", "")
                        or getattr(state, "memory", "")
                        or getattr(state, "thinking", "")
                        or getattr(state, "evaluation_previous_goal", "")
                        or ""
                    )
                if hasattr(output, "action"):
                    for item in (output.action or []):
                        action_type = item.__class__.__name__
                        action_names.append(action_type)
                        payload: dict[str, Any] = {}
                        if hasattr(item, "model_dump"):
                            try:
                                payload = item.model_dump(
                                    mode="json",
                                    exclude_none=True,
                                )
                            except Exception:
                                payload = {}
                        action_details.append({
                            "type": action_type,
                            "data": payload,
                        })
            last_action_summary = _summarize_action_details(action_details)
            await emit(
                "step",
                f"步骤 {step_counter}",
                {
                    "thought": thought,
                    "actions": action_names,
                    "action_details": action_details,
                },
            )
            try:
                screenshot, current_url = await _capture_screenshot(session)
                last_known_url = current_url
                path = artifact_dir / f"step-{step_counter:03d}.png"
                path.write_bytes(screenshot)
                artifact = database.add_artifact(job_id, "screenshot", path)
                event = database.add_event(
                    job_id,
                    "screenshot",
                    f"步骤 {step_counter} 截图",
                    {"artifact_id": artifact["id"], "url": current_url},
                )
                await event_hub.publish(
                    job_id,
                    {
                        **event,
                        "data": {
                            **event["data"],
                            "image": (
                                "data:image/png;base64,"
                                + base64.b64encode(screenshot).decode("ascii")
                            ),
                        },
                    },
                )
            except Exception as screenshot_error:
                await emit("warning", f"截图失败：{screenshot_error}")

        async def on_step_start(_agent: Any) -> None:
            nonlocal last_known_url
            await control.wait_if_paused()
            try:
                current_url = await session.get_current_page_url()
            except Exception:
                current_url = ""
            last_known_url = current_url or last_known_url
            await emit(
                "thinking",
                "Agent 正在分析页面并决定下一步",
                {"url": current_url, "step": step_counter + 1},
            )

        async def heartbeat() -> None:
            nonlocal last_known_url
            while True:
                if control.stop_requested:
                    return
                if not control.resume_event.is_set():
                    await asyncio.sleep(2)
                    continue
                try:
                    current_url = await asyncio.wait_for(
                        session.get_current_page_url(),
                        timeout=3,
                    )
                except Exception:
                    current_url = ""
                last_known_url = current_url or last_known_url
                database.update_job(job_id, last_heartbeat_at=utc_now())
                await emit(
                    "heartbeat",
                    "Agent 正在运行",
                    {"url": current_url, "step": step_counter},
                )
                await asyncio.sleep(5)

        submit_rule = (
            "完成核对后可以点击最终提交按钮。"
            if job["auto_submit"]
            else "绝对不要点击最终提交按钮，只填写答案并停留在页面供人工核对。"
        )
        task = f"""
你是本地答题工作台中的浏览器 Agent。请复用当前账号的登录态，打开以下作业并完成作答：

课程：{job["course_name"]}
作业：{job["assignment_title"]}
作业地址：{job["task_url"]}

要求：
1. 先滚动页面并读取当前实际存在的全部题目和作答控件，建立题目清单。
2. 按页面实际存在的控件处理单选、多选、判断、填空、简答和富文本题；页面没有的题型不要等待或反复查找。
3. 逐题填写，并在填写后确认输入值或选中状态；一个控件确认完成后再处理下一题。
4. 主观题答案要具体、完整、可直接提交。
5. 富文本题不要操作 UEditor 工具栏，直接进入 ueditor iframe 的正文区域填写；绘图题用清晰的 Mermaid/PlantUML 文本和步骤说明表达。
6. {submit_rule}
7. 结束前再次检查所有题目；只要仍有实际题目未填写、未选择或无法确认，就继续处理，不要调用 done。
8. 不要访问与作业无关的页面，不要修改账号设置。
9. 每个关键操作后确认页面状态，遇到弹窗先处理再继续。
"""
        async def should_stop() -> bool:
            return control.stop_requested

        agent_tools = Tools(exclude_actions=[
            "write_file",
            "replace_file",
            "read_file",
            "save_as_pdf",
            "search",
            "switch",
            "close",
        ])
        agent = Agent(
            task=task,
            llm=llm,
            fallback_llm=fallback_llm,
            tools=agent_tools,
            browser_session=session,
            use_vision=not is_deepseek,
            use_judge=False,
            flash_mode=is_deepseek,
            register_new_step_callback=on_step,
            save_conversation_path=str(artifact_dir / "agent-history.json"),
            max_actions_per_step=3 if is_deepseek else 5,
            step_timeout=180,
            max_failures=4,
            directly_open_url=True,
            enable_signal_handler=False,
            llm_timeout=180,
            register_should_stop_callback=should_stop,
        )
        heartbeat_task = asyncio.create_task(heartbeat())
        await emit("agent_ready", "Agent 已初始化，正在读取页面")
        history = await asyncio.wait_for(
            agent.run(
                max_steps=100,
                on_step_start=on_step_start,
            ),
            timeout=3600,
        )
        final_result = ""
        if hasattr(history, "final_result"):
            value = history.final_result()
            final_result = str(value or "")

        captured_url = ""
        try:
            screenshot, current_url = await _capture_screenshot(
                session,
                full_page=True,
            )
            captured_url = current_url
            path = artifact_dir / "final.png"
            path.write_bytes(screenshot)
            artifact = database.add_artifact(job_id, "final_screenshot", path)
            event = database.add_event(
                job_id,
                "final_screenshot",
                "完成截图已保存",
                {"artifact_id": artifact["id"], "url": current_url},
            )
            await event_hub.publish(
                job_id,
                {
                    **event,
                    "data": {
                        **event["data"],
                        "image": (
                            "data:image/png;base64,"
                            + base64.b64encode(screenshot).decode("ascii")
                        ),
                    },
                },
            )
        except Exception as screenshot_error:
            await emit("warning", f"最终截图失败：{screenshot_error}")

        result_path = artifact_dir / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "job": job,
                    "final_result": final_result,
                    "history": history.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        database.add_artifact(job_id, "result_json", result_path)
        agent_success = True
        if hasattr(history, "is_successful"):
            agent_success = bool(history.is_successful())
        if not agent_success:
            submission_status = (
                "submission_unknown"
                if job["auto_submit"]
                else "partial_pending_review"
            )
            final_message = (
                _compact_text(final_result, 1200)
                or "Agent 没有返回可读的完成说明。"
            )
            intervention_summary = (
                "等待人工介入：Agent 未能确认全部题目已经完成。"
                f"已完成 {step_counter} 个动作步骤。"
                f"最后页面：{_compact_text(captured_url or last_known_url or '未知', 240)}。"
                f"最近动作：{_compact_text(last_action_summary or '没有记录到有效动作', 320)}。"
                f"Agent说明：{final_message} "
                "建议：点击“进入实时浏览器”检查未完成题目，处理完毕后点击“结束介入”。"
            )
            await emit(
                "result_summary",
                intervention_summary,
                {
                    "status": "needs_intervention",
                    "steps": step_counter,
                    "current_url": captured_url or last_known_url,
                    "last_action": last_action_summary,
                    "agent_result": final_message,
                },
            )
            if session is not None:
                preserve_session = True
                _intervention_sessions[job_id] = PreservedSession(
                    session=session,
                    process=browser_process,
                    account_lock=account_lock,
                )
                database.transition_job(
                    job_id,
                    "needs_intervention",
                    submission_status=submission_status,
                    progress=95,
                    error=intervention_summary,
                )
                await emit(
                    "needs_intervention",
                    "Agent 未能确认全部题目完成，已保留浏览器等待人工介入",
                    {
                        "result": final_message,
                        "steps": step_counter,
                        "summary": intervention_summary,
                    },
                )
            else:
                database.transition_job(
                    job_id,
                    "incomplete",
                    submission_status=submission_status,
                    progress=95,
                    error=intervention_summary,
                    finished_at=utc_now(),
                )
                await emit(
                    "incomplete",
                    intervention_summary,
                )
            await notify_safely(
                {
                    "event": (
                        "assignment_needs_intervention"
                        if preserve_session
                        else "assignment_incomplete"
                    ),
                    "job_id": job_id,
                    "account": account["name"],
                    "course": job["course_name"],
                    "assignment": job["assignment_title"],
                    "auto_submit": bool(job["auto_submit"]),
                    "submission_status": submission_status,
                    "result": intervention_summary,
                }
            )
            return
        submission_status = (
            "submitted_unverified"
            if job["auto_submit"]
            else "filled_pending_review"
        )
        database.transition_job(
            job_id,
            "completed",
            submission_status=submission_status,
            progress=100,
            finished_at=utc_now(),
        )
        completion_summary = (
            f"任务完成：Agent 报告已执行完成，共完成 {step_counter} 个动作步骤。"
            f"最后页面：{_compact_text(captured_url or last_known_url or '未知', 240)}。"
            f"最近动作：{_compact_text(last_action_summary or '没有记录到动作', 320)}。"
            f"Agent说明：{_compact_text(final_result, 1200) or '未提供额外说明。'}"
        )
        await emit(
            "result_summary",
            completion_summary,
            {
                "status": "completed",
                "steps": step_counter,
                "current_url": captured_url or last_known_url,
                "last_action": last_action_summary,
            },
        )
        await emit("completed", completion_summary)
        await notify_safely(
            {
                "event": "assignment_completed",
                "job_id": job_id,
                "account": account["name"],
                "course": job["course_name"],
                "assignment": job["assignment_title"],
                "auto_submit": bool(job["auto_submit"]),
                "submission_status": submission_status,
                "result": completion_summary,
            }
        )
    except JobStopped as stop_error:
        submission_status = (
            "submission_unknown"
            if job["auto_submit"]
            else "not_submitted"
        )
        stopped_url = last_known_url
        if session is not None and not stopped_url:
            try:
                stopped_url = await asyncio.wait_for(
                    session.get_current_page_url(),
                    timeout=2,
                )
            except Exception:
                stopped_url = ""
        stop_summary = (
            f"任务已停止：{_compact_text(stop_error or '用户请求停止', 300)}。"
            f"停止阶段：{current_stage}。"
            f"已完成 {step_counter} 个动作步骤。"
            f"最后页面：{_compact_text(stopped_url or '未知', 240)}。"
            f"最近动作：{_compact_text(last_action_summary or '没有记录到动作', 320)}。"
        )
        database.transition_job(
            job_id,
            "stopped",
            submission_status=submission_status,
            error=stop_summary,
            finished_at=utc_now(),
        )
        await emit(
            "result_summary",
            stop_summary,
            {
                "status": "stopped",
                "stage": current_stage,
                "current_url": stopped_url,
                "last_action": last_action_summary,
            },
        )
        await emit("stopped", stop_summary)
        await notify_safely(
            {
                "event": "assignment_stopped",
                "job_id": job_id,
                "account": account["name"],
                "course": job["course_name"],
                "assignment": job["assignment_title"],
                "auto_submit": bool(job["auto_submit"]),
                "submission_status": submission_status,
                "result": stop_summary,
            }
        )
    except asyncio.CancelledError:
        current = database.get_job(job_id)
        if current["status"] in {"queued", "running", "paused", "stopping"}:
            forced_summary = (
                "任务已强制终止：停止指令发出后超过时限仍未退出。"
                f"停止阶段：{current_stage}。"
                f"已完成 {step_counter} 个动作步骤。"
                f"最后页面：{_compact_text(last_known_url or '未知', 240)}。"
                f"最近动作：{_compact_text(last_action_summary or '没有记录到动作', 320)}。"
            )
            database.transition_job(
                job_id,
                "stopped",
                submission_status=(
                    "submission_unknown"
                    if job["auto_submit"]
                    else "not_submitted"
                ),
                error=forced_summary,
                finished_at=utc_now(),
            )
            event = database.add_event(
                job_id,
                "stopped",
                forced_summary,
                {
                    "forced": True,
                    "stage": current_stage,
                    "current_url": last_known_url,
                    "last_action": last_action_summary,
                },
            )
            await event_hub.publish(job_id, event)
        raise
    except Exception as error:
        if control.stop_requested:
            submission_status = (
                "submission_unknown"
                if job["auto_submit"]
                else "not_submitted"
            )
            database.transition_job(
                job_id,
                "stopped",
                submission_status=submission_status,
                error="用户请求停止任务，执行器在退出过程中返回了异常。",
                finished_at=utc_now(),
            )
            await emit(
                "result_summary",
                "任务已停止：用户请求停止任务，执行器随后退出。",
                {"status": "stopped", "stage": current_stage},
            )
            await emit("stopped", "任务已按用户请求停止")
        else:
            error_url = last_known_url
            if session is not None and not error_url:
                try:
                    error_url = await asyncio.wait_for(
                        session.get_current_page_url(),
                        timeout=2,
                    )
                except Exception:
                    error_url = ""
            diagnostic = _diagnose_error(
                error,
                stage=current_stage,
                current_url=error_url,
                last_action=last_action_summary,
            )
            await emit(
                "result_summary",
                diagnostic["summary"],
                {
                    "status": "failed",
                    **diagnostic,
                },
            )
            if "登录态" in str(error) or "Cookie" in str(error):
                database.update_account(
                    job["account_id"],
                    status="login_error",
                    last_login_error=str(error),
                )
            submission_status = (
                "submission_unknown"
                if job["auto_submit"]
                else "not_submitted"
            )
            database.transition_job(
                job_id,
                "failed",
                submission_status=submission_status,
                error=diagnostic["summary"],
                finished_at=utc_now(),
            )
            await emit("error", diagnostic["summary"], diagnostic)
            await notify_safely(
                {
                    "event": "assignment_failed",
                    "job_id": job_id,
                    "account": account["name"],
                    "course": job["course_name"],
                    "assignment": job["assignment_title"],
                    "auto_submit": bool(job["auto_submit"]),
                    "submission_status": submission_status,
                    "result": diagnostic["summary"],
                }
            )
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        _job_controls.pop(job_id, None)
        _running_jobs.pop(job_id, None)
        if not preserve_session:
            if session is not None:
                try:
                    await asyncio.wait_for(session.kill(), timeout=8)
                except Exception:
                    pass
            await _terminate_process(browser_process)
            if lock_acquired and account_lock.locked():
                account_lock.release()
            _job_sessions.pop(job_id, None)
