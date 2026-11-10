from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
from pathlib import Path
from typing import Any

import httpx
from browser_use import Agent, BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.llm.openai.chat import ChatOpenAI

from ..config import Settings
from ..db import Database, utc_now
from ..events import EventHub
from .notifier import send_completion_notification
from .preview import preview_manager


_running_jobs: dict[str, asyncio.Task[None]] = {}
_job_controls: dict[str, "JobControl"] = {}
_account_locks: dict[str, asyncio.Lock] = {}

BROWSER_START_TIMEOUT_SECONDS = 45


def _chrome_executable() -> Path:
    candidates = [
        os.getenv("ANSWER_STUDIO_CHROME_PATH", ""),
        shutil.which("chrome") or "",
        shutil.which("chrome.exe") or "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
        ),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("未找到 Chrome 或 Edge，请先安装 Chromium 浏览器")


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
        str(_chrome_executable()),
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
    session_id = await page.session_id
    result = await session.cdp_client.send.Page.captureScreenshot(
        params,
        session_id=session_id,
    )
    return base64.b64decode(result["data"]), await session.get_current_page_url()


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


def pause_job(
    database: Database,
    event_hub: EventHub,
    job_id: str,
) -> None:
    control = _job_controls.get(job_id)
    if not control:
        raise RuntimeError("任务当前未运行")
    control.resume_event.clear()
    database.update_job(job_id, status="paused")
    event = database.add_event(job_id, "paused", "任务已暂停")
    asyncio.create_task(event_hub.publish(job_id, event))


def resume_job(
    database: Database,
    event_hub: EventHub,
    job_id: str,
) -> None:
    control = _job_controls.get(job_id)
    if not control:
        raise RuntimeError("任务当前未运行")
    control.resume_event.set()
    database.update_job(job_id, status="running")
    event = database.add_event(job_id, "resumed", "任务已继续")
    asyncio.create_task(event_hub.publish(job_id, event))


def stop_job(
    database: Database,
    event_hub: EventHub,
    job_id: str,
) -> None:
    control = _job_controls.get(job_id)
    if not control:
        raise RuntimeError("任务当前未运行")
    control.stop_requested = True
    control.stop_event.set()
    control.resume_event.set()
    database.update_job(job_id, status="stopping")
    event = database.add_event(job_id, "stopping", "正在停止任务")
    asyncio.create_task(event_hub.publish(job_id, event))


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
        database.update_job(
            job_id,
            status="running",
            started_at=utc_now(),
            progress=1,
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
            storage_state=account["storage_state_path"],
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
        await emit(
            "browser",
            "可操控浏览器已连接",
            {"interactive": interactive, "pid": browser_process.pid},
        )

        llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            temperature=0.2,
            reasoning_effort=(
                settings.reasoning_effort
                if settings.reasoning_effort
                in {"none", "minimal", "low", "medium", "high", "xhigh"}
                else "medium"
            ),
        )

        async def on_step(
            _browser_state: Any,
            output: Any,
            step: int,
        ) -> None:
            nonlocal step_counter
            step_counter = step + 1
            progress = min(95, 5 + step_counter * 3)
            database.update_job(job_id, progress=progress)
            thought = ""
            action_names: list[str] = []
            if output is not None:
                if hasattr(output, "current_state"):
                    thought = str(getattr(output.current_state, "next_goal", "") or "")
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
                await asyncio.sleep(5)
                if control.stop_requested:
                    return
                if not control.resume_event.is_set():
                    continue
                try:
                    current_url = await session.get_current_page_url()
                except Exception:
                    current_url = ""
                await emit(
                    "heartbeat",
                    "Agent 正在运行",
                    {"url": current_url, "step": step_counter},
                )

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
1. 读取页面中的全部题目和作答控件，逐题完成。
2. 单选、多选、判断、填空、简答和富文本编辑器都要处理。
3. 主观题答案要具体、完整、可直接提交。
4. {submit_rule}
5. 不要访问与作业无关的页面，不要修改账号设置。
6. 每个关键操作后确认页面状态，遇到弹窗先处理再继续。
"""
        async def should_stop() -> bool:
            return control.stop_requested

        agent = Agent(
            task=task,
            llm=llm,
            browser_session=session,
            use_vision=True,
            use_judge=False,
            register_new_step_callback=on_step,
            save_conversation_path=str(artifact_dir / "agent-history.json"),
            max_actions_per_step=5,
            step_timeout=180,
            max_failures=4,
            directly_open_url=True,
            register_should_stop_callback=should_stop,
        )
        heartbeat_task = asyncio.create_task(heartbeat())
        history = await agent.run(
            max_steps=100,
            on_step_start=on_step_start,
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
        database.update_job(
            job_id,
            status="completed",
            progress=100,
            finished_at=utc_now(),
        )
        await emit("completed", final_result or "Agent 已完成")
        await send_completion_notification(
            settings,
            {
                "event": "assignment_completed",
                "job_id": job_id,
                "account": account["name"],
                "course": job["course_name"],
                "assignment": job["assignment_title"],
                "auto_submit": bool(job["auto_submit"]),
                "result": final_result,
            },
        )
    except JobStopped:
        database.update_job(
            job_id,
            status="stopped",
            finished_at=utc_now(),
        )
        await emit("stopped", "任务已停止")
    except Exception as error:
        if control.stop_requested:
            database.update_job(
                job_id,
                status="stopped",
                finished_at=utc_now(),
            )
            await emit("stopped", "任务已按用户请求停止")
        else:
            database.update_job(
                job_id,
                status="failed",
                error=str(error),
                finished_at=utc_now(),
            )
            await emit("error", f"Agent 执行失败：{error}")
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
        _running_jobs.pop(job_id, None)
