from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import os
import platform
import socket
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

import httpx
from browser_use import Agent, BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.config import CONFIG
from browser_use.llm.openai.chat import ChatOpenAI
import browser_use.utils as browser_use_utils

from ..config import Settings
from ..db import Database, utc_now
from ..events import EventHub
from .notifier import send_completion_notification
from .preview import preview_manager
from .browser import chrome_executable


logger = logging.getLogger(__name__)


_running_jobs: dict[str, asyncio.Task[None]] = {}
_job_controls: dict[str, "JobControl"] = {}
_account_locks: dict[str, asyncio.Lock] = {}
_job_sessions: dict[str, BrowserSession] = {}

BROWSER_START_TIMEOUT_SECONDS = 45
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


class _JsonObjectCompletions:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("response_format", {"type": "json_object"})
        response = None
        for attempt in range(3):
            response = await self._delegate.create(*args, **kwargs)
            choices = getattr(response, "choices", None) or []
            if not choices:
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if not content:
                logger.warning(
                    "Model returned empty content on attempt %s",
                    attempt + 1,
                )
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            cleaned = _clean_json_content(content)
            if cleaned == content:
                try:
                    json.loads(cleaned)
                except json.JSONDecodeError:
                    logger.warning(
                        "Model returned non-JSON content (length=%s): %s",
                        len(cleaned),
                        cleaned[:1200],
                    )
            if cleaned != content and message is not None:
                message.content = cleaned
            return response
        return response


def _clean_json_content(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidate = text[start:end + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
    return text


class _JsonObjectChat:
    def __init__(self, delegate: Any) -> None:
        self.completions = _JsonObjectCompletions(delegate.completions)


class _JsonObjectClient:
    def __init__(self, delegate: Any) -> None:
        self.chat = _JsonObjectChat(delegate.chat)


class JsonObjectChatOpenAI(ChatOpenAI):
    def get_client(self) -> Any:
        return _JsonObjectClient(super().get_client())


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
        "--disable-blink-features=AutomationControlled",
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
    await session.cdp_client.send.Page.bringToFront(
        session_id=session_id,
    )
    return True


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

    async def emit(
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
        llm_class = JsonObjectChatOpenAI if is_deepseek else ChatOpenAI
        llm_params: dict[str, Any] = {
            "model": settings.openai_model,
            "api_key": settings.openai_api_key,
            "base_url": settings.openai_base_url,
            "temperature": 0.1,
            "frequency_penalty": 0,
            "timeout": 180,
            "max_retries": 1,
            "max_completion_tokens": 8192,
            "reasoning_effort": (
                settings.reasoning_effort
                if settings.reasoning_effort
                in {"none", "minimal", "low", "medium", "high", "xhigh"}
                else "medium"
            ),
        }
        if is_deepseek:
            llm_params.update({
                "add_schema_to_system_prompt": True,
                "dont_force_structured_output": True,
            })
        llm = llm_class(**llm_params)
        fallback_model = (
            settings.openai_fallback_model.strip()
            or settings.openai_model
        )
        fallback_is_deepseek = "deepseek" in fallback_model.lower()
        fallback_class = (
            JsonObjectChatOpenAI
            if fallback_is_deepseek
            else ChatOpenAI
        )
        fallback_params = {
            **llm_params,
            "model": fallback_model,
            "temperature": 0,
            "max_retries": 2,
        }
        if fallback_is_deepseek:
            fallback_params.update({
                "add_schema_to_system_prompt": True,
                "dont_force_structured_output": True,
            })
        else:
            fallback_params.pop("add_schema_to_system_prompt", None)
            fallback_params.pop("dont_force_structured_output", None)
        fallback_llm = fallback_class(**fallback_params)

        async def on_step(
            _browser_state: Any,
            output: Any,
            step: int,
        ) -> None:
            nonlocal step_counter
            step_counter = step + 1
            progress = min(95, 5 + step_counter * 3)
            database.update_job(
                job_id,
                progress=progress,
                last_heartbeat_at=utc_now(),
            )
            thought = ""
            action_names: list[str] = []
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
                    action_names = [
                        item.__class__.__name__
                        for item in (output.action or [])
                    ]
            await emit(
                "step",
                f"步骤 {step_counter}",
                {"thought": thought, "actions": action_names},
            )
            try:
                screenshot, current_url = await _capture_screenshot(session)
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
            await control.wait_if_paused()
            try:
                current_url = await session.get_current_page_url()
            except Exception:
                current_url = ""
            await emit(
                "thinking",
                "Agent 正在分析页面并决定下一步",
                {"url": current_url, "step": step_counter + 1},
            )

        async def heartbeat() -> None:
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
5. {submit_rule}
6. 结束前再次检查所有题目；只要仍有实际题目未填写、未选择或无法确认，就继续处理，不要调用 done。
7. 不要访问与作业无关的页面，不要修改账号设置。
8. 每个关键操作后确认页面状态，遇到弹窗先处理再继续。
"""
        async def should_stop() -> bool:
            return control.stop_requested

        agent = Agent(
            task=task,
            llm=llm,
            fallback_llm=fallback_llm,
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

        try:
            screenshot, current_url = await _capture_screenshot(
                session,
                full_page=True,
            )
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
            database.transition_job(
                job_id,
                "incomplete",
                submission_status=submission_status,
                progress=95,
                error="Agent 报告任务未完成",
                finished_at=utc_now(),
            )
            await emit(
                "incomplete",
                final_result or "Agent 报告任务未完成",
            )
            await notify_safely(
                {
                    "event": "assignment_incomplete",
                    "job_id": job_id,
                    "account": account["name"],
                    "course": job["course_name"],
                    "assignment": job["assignment_title"],
                    "auto_submit": bool(job["auto_submit"]),
                    "submission_status": submission_status,
                    "result": final_result or "Agent 报告任务未完成",
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
        await emit("completed", final_result or "Agent 已完成")
        await notify_safely(
            {
                "event": "assignment_completed",
                "job_id": job_id,
                "account": account["name"],
                "course": job["course_name"],
                "assignment": job["assignment_title"],
                "auto_submit": bool(job["auto_submit"]),
                "submission_status": submission_status,
                "result": final_result,
            }
        )
    except JobStopped:
        submission_status = (
            "submission_unknown"
            if job["auto_submit"]
            else "not_submitted"
        )
        database.transition_job(
            job_id,
            "stopped",
            submission_status=submission_status,
            finished_at=utc_now(),
        )
        await emit("stopped", "任务已停止")
        await notify_safely(
            {
                "event": "assignment_stopped",
                "job_id": job_id,
                "account": account["name"],
                "course": job["course_name"],
                "assignment": job["assignment_title"],
                "auto_submit": bool(job["auto_submit"]),
                "submission_status": submission_status,
                "result": "任务已停止",
            }
        )
    except asyncio.CancelledError:
        current = database.get_job(job_id)
        if current["status"] in {"queued", "running", "paused", "stopping"}:
            database.transition_job(
                job_id,
                "stopped",
                submission_status=(
                    "submission_unknown"
                    if job["auto_submit"]
                    else "not_submitted"
                ),
                error="任务停止超时，已强制终止",
                finished_at=utc_now(),
            )
            event = database.add_event(
                job_id,
                "stopped",
                "任务已强制终止",
                {"forced": True},
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
                finished_at=utc_now(),
            )
            await emit("stopped", "任务已按用户请求停止")
        else:
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
                error=str(error),
                finished_at=utc_now(),
            )
            await emit("error", f"Agent 执行失败：{error}")
            await notify_safely(
                {
                    "event": "assignment_failed",
                    "job_id": job_id,
                    "account": account["name"],
                    "course": job["course_name"],
                    "assignment": job["assignment_title"],
                    "auto_submit": bool(job["auto_submit"]),
                    "submission_status": submission_status,
                    "result": f"Agent 执行失败：{error}",
                }
            )
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        if session is not None:
            try:
                await asyncio.wait_for(session.kill(), timeout=8)
            except Exception:
                pass
        await _terminate_process(browser_process)
        if lock_acquired and account_lock.locked():
            account_lock.release()
        _job_controls.pop(job_id, None)
        _job_sessions.pop(job_id, None)
        _running_jobs.pop(job_id, None)
