from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
import re
from pathlib import Path

from ...db import utc_now
from ..platforms.catalog import platform_spec, platform_url
from ..tasks.runner import JobControl, JobStopped, _account_lock, _acquire_account_lock
from ..notifications.delivery import notification_preferences, send_completion_notification
from ..notifications.weixin import record_delivery
from ..runtime.preview import preview_manager
from .browser import create_learning_browser
from ...preferences import load_preferences
from .store import ACTIVE, LearningStore
from ...diagnostics.logging import current_trace as diagnostic_context
from ...diagnostics.probe import capture_page as capture_diagnostic_page


class LearningService:
    def __init__(self, database, settings, hub, browser_factory=create_learning_browser, settings_provider=None):
        self._database = database if callable(database) else lambda: database
        self.settings, self.hub, self.browser_factory = settings, hub, browser_factory
        self.settings_provider = settings_provider or (lambda: settings)
        self.tasks, self.controls, self.browsers, self.operations = {}, {}, {}, {}
        self.browser_tickets = {}
        self.auto_retry_limit = 3
        self.shutting_down = False

    def browser_ticket(self, identifier):
        if identifier not in self.browsers: raise RuntimeError("当前任务浏览器已关闭或尚未就绪")
        now = time.monotonic()
        self.browser_tickets = {key: value for key, value in self.browser_tickets.items() if value[1] > now}
        token = secrets.token_urlsafe(32)
        self.browser_tickets[token] = (identifier, now + 30)
        return {"token": token}

    def authorize_browser(self, identifier, token):
        ticket = self.browser_tickets.pop(token, None)
        return bool(ticket and ticket[0] == identifier and ticket[1] > time.monotonic() and identifier in self.browsers)

    @property
    def store(self): return LearningStore(self._database())

    def initialize(self):
        self.shutting_down = False
        self.store.initialize()
        self.store.recover()
        for run in self.store.retryable_interrupted(self.auto_retry_limit):
            if self._retryable_interruption(run.get("error", "")):
                self._resume_interrupted(run["id"])

    def payload(self, run):
        identifier, status = run["id"], run["status"]
        actions = []
        if identifier in self.controls:
            if status in {"queued", "running"}: actions.append("pause")
            if status == "paused": actions.append("resume")
            if status == "awaiting_user": actions.append("continue")
            if status in {"queued", "running", "paused", "awaiting_user"}: actions.append("stop")
        browser = self.browsers.get(identifier)
        lost = getattr(browser, "connection_lost", None)
        available = browser is not None and not (isinstance(lost, asyncio.Event) and lost.is_set())
        if available: actions.append("view_browser" if run.get("headless") else "open_browser")
        if status in {"stopped", "incomplete", "interrupted"}: actions.append("retry")
        if status not in ACTIVE and identifier not in self.tasks and identifier not in self.controls and browser is None and not run.get("deleted_at"):
            actions.append("delete")
        return {**run, "actions": actions, "browser_available": available, "browser_scope": "task", "browser_task_id": identifier}

    def delete_task(self, identifier):
        run = self.store.run(identifier)
        attempts = [item for item in self.store.runs(run["account_id"], include_deleted=True) if item["course_id"] == run["course_id"]]
        if any(item["id"] in self.tasks or item["id"] in self.controls or item["id"] in self.browsers for item in attempts):
            raise RuntimeError("请先停止网课任务，再删除任务。")
        return self.store.delete_task(identifier)

    def active(self, account_id):
        for identifier in list(self.browsers):
            run = self.store.run(identifier)
            if run["account_id"] == account_id: return self.payload(run)
        run = self.store.active(account_id)
        return self.payload(run) if run else None

    async def emit(self, identifier, kind, message, data=None):
        event = self.store.event(identifier, kind, message, data)
        await self.hub.publish(f"learning:{identifier}", event)
        return event

    async def collect(self, account_id, year):
        account = self._database().get_account(account_id)
        if not platform_spec(account["platform"])["academic_year_filter"]: year = 0
        async with _account_lock(account_id):
            browser = self.browser_factory(account, headless=True)
            await preview_manager.invalidate(account_id)
            try:
                await browser.open()
                courses = await browser.discover_courses(year)
                return self.store.upsert_courses(account_id, courses, year)
            except RuntimeError as error:
                if "登录" in str(error) or "Cookie" in str(error):
                    self._database().update_account(account_id, status="login_error", last_login_error=str(error)[:500])
                raise
            finally:
                await browser.close()

    def start(self, course_id):
        run = self.store.create_run(course_id, headless=load_preferences(self._database()).learning.headless)
        self._launch_run(run["id"])
        return self.payload(run)

    def _launch_run(self, identifier, retry_reason=None):
        existing = self.tasks.get(identifier)
        if existing and not existing.done():
            return
        self.controls[identifier] = JobControl()
        self.controls[identifier].learning_revision = 0
        self.tasks[identifier] = asyncio.create_task(self._supervise_run(identifier, retry_reason))

    def _resume_interrupted(self, identifier):
        try:
            self.store.reopen_for_retry(identifier, reason="服务重启后自动恢复", limit=self.auto_retry_limit)
        except (KeyError, RuntimeError):
            return
        self._launch_run(identifier, "服务重启后自动恢复")

    @staticmethod
    def _retryable_interruption(error):
        text = str(error or "")
        blocked = (
            "原任务状态为 paused", "原任务状态为 awaiting_user", "原任务状态为 stopping",
            "登录", "Cookie", "授权", "账号", "人工确认", "用户停止",
        )
        return not any(marker.lower() in text.lower() for marker in blocked)

    async def _supervise_run(self, identifier, initial_retry_reason=None):
        try:
            if initial_retry_reason:
                current = self.store.run(identifier)
                await self.emit(identifier, "auto_retry", f"{initial_retry_reason}（{current['auto_retry_count']}/{self.auto_retry_limit}）", {
                    "attempt": current["auto_retry_count"],
                    "limit": self.auto_retry_limit,
                    "reason": initial_retry_reason,
                })
            while True:
                control = self.controls.get(identifier)
                await self.run(identifier)
                run = self.store.run(identifier)
                if self.shutting_down or run["status"] != "interrupted":
                    break
                if getattr(control, "manual_hold", False) or not self._retryable_interruption(run.get("error")):
                    self.store.update(identifier, phase="interrupted")
                    break
                retry_count = int(run.get("auto_retry_count") or 0)
                if retry_count >= self.auto_retry_limit:
                    detail = str(run.get("error") or "网课执行中断")
                    message = f"{detail}；已自动重试 {self.auto_retry_limit} 次，仍未恢复。"
                    self.store.update(identifier, error=message)
                    await self.emit(identifier, "retry_exhausted", message, {
                        "attempt": retry_count, "limit": self.auto_retry_limit,
                    })
                    break
                try:
                    next_run = self.store.reopen_for_retry(
                        identifier, reason=run.get("error"), limit=self.auto_retry_limit,
                    )
                except (KeyError, RuntimeError):
                    break
                self.controls[identifier] = JobControl()
                self.controls[identifier].learning_revision = 0
                await self.emit(identifier, "auto_retry", f"网课连接异常，正在自动重试（{next_run['auto_retry_count']}/{self.auto_retry_limit}）", {
                    "attempt": next_run["auto_retry_count"],
                    "limit": self.auto_retry_limit,
                    "reason": str(run.get("error") or "")[:500],
                })
                await asyncio.sleep(1)
        finally:
            self.tasks.pop(identifier, None)
            if not self.shutting_down:
                await self.notify(identifier)

    async def control(self, identifier, action):
        async with self.operations.setdefault(identifier, asyncio.Lock()):
            run = self.store.run(identifier)
            if action not in self.payload(run)["actions"]: raise RuntimeError("当前状态不支持此操作")
            control, browser = self.controls.get(identifier), self.browsers.get(identifier)
            if action == "view_browser":
                if not browser: raise RuntimeError("任务浏览器已关闭")
            elif action == "open_browser":
                await browser.focus()
            elif action == "pause":
                control.manual_hold = True
                control.resume_event.clear()
                control.paused_event.set()
                try:
                    if browser: await asyncio.wait_for(browser.pause(), 5)
                except Exception:
                    control.manual_hold = False
                    control.paused_event.clear(); control.resume_event.set()
                    raise RuntimeError("未确认播放器暂停，请进入浏览器检查")
                self.store.update(identifier, status="paused", paused_from=run["status"])
                await self.emit(identifier, "paused", "用户已暂停，播放器和任务切换已停止")
            elif action in {"resume", "continue"}:
                control.manual_hold = False
                target = "queued" if run["status"] == "paused" and run["paused_from"] == "queued" else "running"
                self.store.update(identifier, status=target, error=None, paused_from=None)
                control.learning_revision += 1
                control.paused_event.clear(); control.resume_event.set()
                await self.emit(identifier, "resumed", "继续当前课程，重新核验平台进度")
            elif action == "stop":
                control.stop_requested = True
                control.stop_event.set(); control.resume_event.set()
                self.store.update(identifier, status="stopping", phase="stopping")
                await self.emit(identifier, "stopping", "正在停止网课执行并关闭任务浏览器")
                asyncio.create_task(self._force_stop(identifier))
            elif action == "retry":
                return self.start(run["course_id"])
            return self.payload(self.store.run(identifier))

    async def _force_stop(self, identifier):
        task = self.tasks.get(identifier)
        if not task: return
        try: await asyncio.wait_for(asyncio.shield(task), 10)
        except TimeoutError: task.cancel()

    async def watch_browser_connection(self, identifier, browser, control, owner):
        signal = getattr(browser, "connection_lost", None)
        if not isinstance(signal, asyncio.Event): return
        await signal.wait()
        if control.stop_requested or self.store.run(identifier)["status"] not in ACTIVE: return
        reason = browser.close_reason or "网课浏览器连接断开，任务已中断"
        control.browser_failure_reason = reason
        self._database().diagnostics.trace("learning", identifier).record("browser.connection_lost", reason,
            level="error", data={"cause": browser.failure_kind, "headless": bool(self.store.run(identifier).get("headless"))})
        await self.emit(identifier, "browser_closed", reason, {"cause": browser.failure_kind})
        if not owner.done(): owner.cancel(reason)

    async def wait_for_user(self, identifier, browser, control, message, *, pause_media=True):
        control.manual_hold = True
        if pause_media:
            await browser.pause()
        diagnostics = self._database().diagnostics.trace("learning", identifier)
        if diagnostics.available:
            try:
                attachments, frames = await browser.inspect()
                diagnostics.snapshot("intervention-resources", {"attachments": list(attachments.values()), "frames": [item["data"] for item in frames]})
                for index, item in enumerate(frames[:8]): await capture_diagnostic_page(diagnostics, item["frame"], f"failure-frame-{index}")
            except Exception as error: diagnostics.record("diagnostics.capture_failed", str(error), level="warning")
        control.resume_event.clear(); control.paused_event.set()
        self.store.update(identifier, status="awaiting_user", phase="awaiting_user", error=message)
        await self.emit(identifier, "awaiting_user", message)
        await control.wait_if_paused()

    async def verify_catalog(self, identifier, browser, control):
        for attempt in range(3):
            try: return await control.run(browser.verify_catalog)
            except (JobStopped, asyncio.CancelledError): raise
            except Exception as error:
                if hasattr(browser, "ensure_connected"): browser.ensure_connected()
                message = str(error)
                transient = isinstance(error, TimeoutError) or bool(re.search(r"timeout|timed out|HTTP (?:408|429|5\d\d)|ERR_(?:CONNECTION|NETWORK|TIMED_OUT)|连接.*(?:失败|超时)", message, re.I))
                if not transient or attempt == 2: raise
                await self.emit(identifier, "network_retry", f"平台进度读取暂时失败，正在重试（{attempt + 1}/2）", {"attempt": attempt + 1, "reason": message})
                await control.run(lambda: asyncio.sleep(.5 * (attempt + 1)))

    async def heartbeat(self, identifier):
        while True:
            self.store.update(identifier, heartbeat_at=utc_now())
            await asyncio.sleep(5)

    async def run(self, identifier):
        run = self.store.run(identifier)
        account = self._database().get_account(run["account_id"])
        diagnostics = self._database().diagnostics.trace("learning", identifier)
        diagnostics.configure(run, account, self.settings_provider())
        log_context = diagnostic_context.set(diagnostics)
        control = self.controls[identifier]
        browser, acquired = None, False
        browser_watch = None
        lock = asyncio.Lock()
        heartbeat = asyncio.create_task(self.heartbeat(identifier))
        try:
            await self.emit(identifier, "queued", "网课任务已创建，正在准备独立任务浏览器")
            await _acquire_account_lock(lock, control)
            acquired = True
            await control.wait_if_paused()
            self.store.update(identifier, status="running", phase="browser", started_at=utc_now())
            browser = self.browser_factory({**account, "_task_id": identifier}, headless=bool(run["headless"]))
            browser.learning_options = load_preferences(self._database()).learning.model_dump()
            browser.diagnostics = diagnostics
            if hasattr(browser, "workspace"):
                diagnostics.record("browser.workspace", "Task browser isolated", data={"kind": "learning", "task_id": identifier, "profile": browser.workspace.path.name})
            await control.run(browser.open)
            self.browsers[identifier] = browser
            browser_watch = asyncio.create_task(self.watch_browser_connection(identifier, browser, control, asyncio.current_task()))
            await self.emit(identifier, "browser_ready", "后台浏览器已就绪，可在软件内实时操控" if run["headless"] else "网课独立窗口已就绪", {"headless": bool(run["headless"]), "browser_task_id": identifier})
            await control.run(lambda: browser.navigate(run["url"]))
            scope, completed = set(), set()
            resume_chapter_id = None
            while True:
                chapters = await self.verify_catalog(identifier, browser, control)
                self.store.save_chapters(run["course_id"], chapters)
                targets = [chapter for chapter in chapters if not chapter["completed"]]
                scope.update(item["chapterId"] for item in targets)
                completed.update(item["chapterId"] for item in chapters if item["completed"] and item["chapterId"] in scope)
                self.store.update(identifier, target_json=json.dumps(sorted(scope)), completed_json=json.dumps(sorted(completed)), phase="learning")
                await self.emit(identifier, "catalog", f"读取到 {len(chapters)} 个章节，跳过 {len(chapters) - len(targets)} 个已完成章节，剩余 {len(targets)} 个待处理")
                deferred_chapters = []
                last_chapter_id = None
                for chapter in targets:
                    await control.wait_if_paused()
                    fresh = await self.verify_catalog(identifier, browser, control)
                    current = next((item for item in fresh if item["chapterId"] == chapter["chapterId"]), None)
                    if current is None: raise RuntimeError("课程目录已变化，请重新采集课程")
                    if current["completed"]:
                        completed.add(chapter["chapterId"])
                        self.store.update(identifier, completed_json=json.dumps(sorted(completed)), progress=int(len(completed) / max(1, len(scope)) * 95))
                        await self.emit(identifier, "chapter_skipped", f"跳过已完成章节：{chapter['title']}")
                        continue
                    self.store.update(identifier, current_chapter=chapter["title"], current_unit=None, position=0, duration=0)
                    await self.emit(identifier, "chapter", f"进入章节：{chapter['title']}")
                    try:
                        if chapter["chapterId"] != resume_chapter_id:
                            await control.run(lambda: browser.open_chapter(chapter["chapterId"]))
                        resume_chapter_id = None
                        last_chapter_id = chapter["chapterId"]
                        pending = await self.learn_chapter(identifier, chapter, browser, control)
                    except (JobStopped, asyncio.CancelledError): raise
                    except Exception as error:
                        if hasattr(browser, "ensure_connected"): browser.ensure_connected()
                        if any(term in str(error) for term in ("登录", "Cookie", "账号", "平台进度核验")): raise
                        diagnostics.exception(error, phase="chapter")
                        pending = {"chapter_id": chapter["chapterId"], "title": chapter["title"], "resources": [{"reason": str(error)}]}
                    if pending:
                        deferred_chapters.append(pending)
                        await self.emit(identifier, "chapter_deferred", f"已暂缓：{chapter['title']}，继续下一章节", pending)
                    else:
                        completed.add(chapter["chapterId"])
                        self.store.update(identifier, completed_json=json.dumps(sorted(completed)), progress=int(len(completed) / max(1, len(scope)) * 95))
                        await self.emit(identifier, "chapter_verified", f"平台已确认章节完成：{chapter['title']}")
                await control.wait_if_paused()
                fresh = await self.verify_catalog(identifier, browser, control)
                if fresh and all(item["completed"] for item in fresh) and hasattr(browser,"advance_catalog"):
                    if await control.run(browser.advance_catalog):
                        await self.emit(identifier,"catalog_advanced","当前目录已核验完成，正在读取下一章节目录")
                        continue
                if fresh and all(item["completed"] for item in fresh): break
                unresolved = [item for item in fresh if not item["completed"]]
                self.store.save_chapters(run["course_id"], fresh)
                completed.difference_update(item["chapterId"] for item in unresolved)
                completed.update(item["chapterId"] for item in fresh if item["completed"] and item["chapterId"] in scope)
                self.store.update(identifier, completed_json=json.dumps(sorted(completed)), progress=int(len(completed) / max(1, len(scope)) * 95))
                await self.emit(identifier, "pending_summary", f"本轮已处理其余资源，仍有 {len(unresolved)} 个章节未通过平台核验", {"chapters": deferred_chapters, "remaining_ids": [item["chapterId"] for item in unresolved]})
                next_resume = last_chapter_id
                if unresolved and unresolved[0]["chapterId"] != last_chapter_id:
                    try:
                        await control.run(lambda: browser.open_chapter(unresolved[0]["chapterId"]))
                        next_resume = unresolved[0]["chapterId"]
                    except (JobStopped, asyncio.CancelledError): raise
                    except Exception as error: diagnostics.record("learning.return_to_pending_failed", str(error), level="warning")
                titles = "、".join(item["title"] for item in unresolved[:3])
                reasons = [resource.get("reason", "") for item in deferred_chapters for resource in item.get("resources", [])]
                reason = reasons[0][:180] if reasons else "平台尚未确认完成"
                await self.wait_for_user(identifier, browser, control, f"其余可处理资源已执行，剩余 {len(unresolved)} 个未完成章节：{titles}。原因：{reason}。处理后继续；暂缓项没有计为完成。", pause_media=False)
                resume_chapter_id = next_resume
            self.store.save_chapters(run["course_id"], fresh)
            if not fresh or not all(item["completed"] for item in fresh):
                self.store.update(identifier, status="incomplete", phase="incomplete", error="平台仍有未完成或状态未知的章节，请查看课程目录后重试", finished_at=utc_now())
                await self.emit(identifier, "incomplete", "平台复验仍存在未完成章节，本次未标记完成")
            else:
                await self.capture_evidence(identifier, browser, control)
                await control.wait_if_paused()
                self.store.update(identifier, status="completed", phase="verified", progress=100, error=None, finished_at=utc_now())
                await self.emit(identifier, "completed", "全部章节已通过平台复验，课程执行完成")
        except (JobStopped, asyncio.CancelledError):
            status = "stopped" if control.stop_requested else "interrupted"
            current = self.store.run(identifier)
            if current["status"] in ACTIVE:
                if status == "stopped" and current["status"] != "stopping": self.store.update(identifier, status="stopping")
                reason = "用户停止任务" if status == "stopped" else getattr(control, "browser_failure_reason", None) or "本地服务退出，网课执行已中断"
                phase = "auto_retry_pending" if status == "interrupted" and not getattr(control, "manual_hold", False) else status
                self.store.update(identifier, status=status, phase=phase, error=reason, finished_at=utc_now())
                await self.emit(identifier, status, "网课任务已停止" if status == "stopped" else reason)
        except Exception as error:
            diagnostics.exception(error, phase=self.store.run(identifier).get("phase"))
            if browser and getattr(browser, "page", None) and not browser.page.is_closed():
                try: await capture_diagnostic_page(diagnostics, browser.page, "failure-page")
                except Exception: diagnostics.record("diagnostics.capture_failed", "Browser unavailable for failure capture", level="warning")
            current = self.store.run(identifier)
            if current["status"] in ACTIVE:
                status = "stopped" if control.stop_requested else "incomplete" if isinstance(error, ValueError) else "interrupted"
                signal = getattr(browser, "connection_lost", None)
                reason = browser.close_reason if isinstance(signal, asyncio.Event) and signal.is_set() else str(error)[:1600]
                phase = "auto_retry_pending" if status == "interrupted" and not getattr(control, "manual_hold", False) else status
                self.store.update(identifier, status=status, phase=phase, error=reason, finished_at=utc_now())
                await self.emit(identifier, status, reason)
                if "登录" in str(error) or "Cookie" in str(error):
                    self._database().update_account(account["id"], status="login_error", last_login_error=str(error)[:500])
        finally:
            if browser_watch:
                browser_watch.cancel()
                await asyncio.gather(browser_watch, return_exceptions=True)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            try:
                if browser and self.store.run(identifier)["status"] != "completed" and (not hasattr(browser, "page") or (browser.page and browser.page.url != "about:blank")):
                    try:
                        snapshot = await asyncio.wait_for(browser.snapshot(), 3)
                        folder = self.settings.artifacts_root / "learning" / identifier
                        folder.mkdir(parents=True, exist_ok=True)
                        path = folder / "last-page.jpg"
                        path.write_bytes(base64.b64decode(snapshot["image"].split(",", 1)[1]))
                        self.store.add_artifact(identifier, path)
                    except Exception:
                        pass
                if browser: await browser.close()
            except Exception as error:
                await self.emit(identifier, "warning", f"浏览器退出异常：{error}")
            finally:
                self.browsers.pop(identifier, None)
                if acquired: lock.release()
                self.controls.pop(identifier, None)
            diagnostics.summary(self.store.run(identifier))
            diagnostic_context.reset(log_context)

    async def learn_chapter(self, identifier, chapter, browser, control):
        played, ended = set(), {}
        attempts, play_errors, healthy_since = {}, {}, {}
        positions, progress_at, read_done, read_started = {}, {}, {}, {}
        deferred, announced = {}, set()
        verify_at, missing_since = 0, None
        revision = control.learning_revision
        options = getattr(browser, "learning_options", {})
        resource_wait = options.get("resource_wait_seconds", 15)
        confirmation_wait = options.get("confirmation_wait_seconds", 25)

        async def defer(key, title, reason, kind="unknown"):
            if key not in deferred:
                deferred[key] = {"resource_id": key, "title": title, "kind": kind, "reason": reason}
                await self.emit(identifier, "resource_deferred", f"暂缓当前任务点，继续其余资源：{title}（{reason}）", deferred[key])

        def kind(item):
            data = item["data"]
            detected = data.get("resource", {}).get("kind", "unknown")
            if detected != "unknown": return detected
            if data.get("read"): return "document"
            if data.get("media"): return "media"
            if data.get("quiz"): return "quiz"
            return "unknown"

        while True:
            await control.wait_if_paused()
            now = asyncio.get_running_loop().time()
            if revision != control.learning_revision:
                played.clear(); ended.clear(); attempts.clear(); play_errors.clear(); healthy_since.clear()
                positions.clear(); progress_at.clear(); read_done.clear(); read_started.clear(); deferred.clear()
                revision = control.learning_revision
                verify_at, missing_since = 0, None
            if now >= verify_at:
                fresh = await self.verify_catalog(identifier, browser, control)
                current = next((item for item in fresh if item["chapterId"] == chapter["chapterId"]), None)
                if current is None: raise RuntimeError("课程目录已变化，无法核对当前章节")
                if current["completed"]: return None
                verify_at = now + 6
            attachments, frames = await control.run(browser.inspect)
            if any(item["data"].get("challenge") for item in frames):
                await self.wait_for_user(identifier, browser, control, "当前页面有身份验证，请在实时浏览器处理后继续。")
                continue
            pending = {key: unit for key, unit in attachments.items() if unit["mandatory"] and not unit["completed"]}
            for key, unit in attachments.items():
                if key not in pending and key not in announced:
                    announced.add(key)
                    await self.emit(identifier, "resource_skipped", f"跳过{'已完成' if unit['completed'] else '非任务点'}：{unit['title']}", {"resource_id": key, "completed": unit["completed"]})
            for key, since in list(read_done.items()):
                if key in pending and now - since > confirmation_wait:
                    await defer(key, pending[key]["title"], "阅读操作已执行，但平台尚未确认完成", pending[key].get("kind", "document"))
            candidates = [item for item in frames if item["job_id"] in pending and item["job_id"] not in deferred
                          and item["job_id"] not in read_done and kind(item) != "unknown"]
            candidates.sort(key=lambda item: (item["job_id"] not in ended, bool(item["data"].get("resource", {}).get("ready"))), reverse=True)
            candidate = candidates[0] if candidates else None
            if candidate is None:
                missing_since = now if missing_since is None else missing_since
                exhausted = bool(pending) and all(key in deferred for key in pending)
                wait = confirmation_wait if read_done or ended else resource_wait
                if not exhausted and now - missing_since < wait:
                    await control.run(lambda: asyncio.sleep(1)); continue
                for key, unit in pending.items():
                    await defer(key, unit["title"], "任务点尚未加载出受支持的控件或未得到平台完成确认", unit.get("kind", "unknown"))
                if hasattr(browser, "advance_resource_card") and await control.run(browser.advance_resource_card):
                    await self.emit(identifier, "resource_tab", "当前资源页已处理，正在检查下一资源页")
                    missing_since, verify_at = None, 0
                    continue
                fresh = await self.verify_catalog(identifier, browser, control)
                if next((item for item in fresh if item["chapterId"] == chapter["chapterId"] and item["completed"]), None): return None
                if not deferred:
                    reason = "当前页有章节测验，待处理后核验" if any(item["data"].get("quiz") for item in frames) else "目录仍显示未完成，但当前页没有可确认的剩余任务点"
                    await defer("chapter:" + chapter["chapterId"], chapter["title"], reason)
                return {"chapter_id": chapter["chapterId"], "title": chapter["title"], "resources": list(deferred.values())}
            missing_since = None
            unit = pending[candidate["job_id"]]
            key, data, resource_kind = unit["id"], candidate["data"], kind(candidate)
            if data.get("error"):
                await defer(key, unit["title"], data["error"], resource_kind); continue
            if data.get("quiz"):
                await defer(key, unit["title"], "章节或视频练习需要处理", "quiz"); continue
            self.store.update(identifier, phase="learning", current_unit=unit["title"])
            if resource_kind != "media":
                self.store.update(identifier, position=0, duration=0)
                read_started.setdefault(key, now)
                if not hasattr(browser, "read_resource"):
                    await defer(key, unit["title"], "当前资源没有对应的阅读处理器", resource_kind); continue
                if key not in announced:
                    announced.add(key)
                    await self.emit(identifier, "reading", f"正在处理阅读资源：{unit['title']}", {"resource_id": key, "kind": resource_kind})
                try:
                    result = await control.run(lambda: browser.read_resource(candidate["frame"]))
                except Exception as error:
                    if control.paused_event.is_set():
                        await control.wait_if_paused(); continue
                    if hasattr(browser, "ensure_connected"): browser.ensure_connected()
                    if isinstance(error, TimeoutError): await control.run(browser.pause)
                    result = {"unsupported": True, "reason": str(error)}
                if result.get("handled"):
                    read_done[key] = asyncio.get_running_loop().time()
                    verify_at = 0
                    await self.emit(identifier, "resource_verifying", f"阅读操作已执行，等待平台确认：{unit['title']}", {"resource_id": key, "result": result})
                elif result.get("unsupported") and now - read_started[key] >= resource_wait:
                    await defer(key, unit["title"], result.get("reason", "阅读器不受支持"), resource_kind)
                elif result.get("waiting") and now - read_started[key] > max(resource_wait, result.get("wait_seconds", 0) * 3 + confirmation_wait):
                    await defer(key, unit["title"], "阅读控件或计时阅读等待超时", resource_kind)
                await control.run(lambda: asyncio.sleep(1))
                continue
            media = data["media"]
            self.store.update(identifier, position=media["position"], duration=media["duration"])
            if media["ended"]:
                if key not in ended:
                    ended[key] = now
                    await self.emit(identifier, "verifying", f"播放结束，等待平台确认：{unit['title']}")
                if now - ended[key] > confirmation_wait:
                    await defer(key, unit["title"], "视频已结束，但平台尚未确认完成", "media")
            elif key not in played or media["paused"]:
                if media["paused"]:
                    healthy_since.pop(key, None)
                    count = attempts.get(key, 0)
                    if count >= 3:
                        await defer(key, unit["title"], "自动恢复 3 次仍未保持播放：" + play_errors.get(key, "播放器启动后再次暂停"), "media")
                        continue
                    attempts[key] = count + 1
                    if count or key in played:
                        await self.emit(identifier, "playback_recovering", f"播放器暂停，自动恢复（{count + 1}/3）：{unit['title']}", {"resource_id": key, "attempt": count + 1, "limit": 3})
                self._database().diagnostics.trace("learning", identifier).record("learning.media_selected", data={"resource_id": key, "chapter_id": chapter["chapterId"], "position": media["position"]}, component="learning_executor")
                result = {"started": True, "adopted": True} if not media["paused"] else await control.run(lambda: browser.play(candidate["frame"]))
                if not result.get("started"):
                    _, fresh_frames = await control.run(browser.inspect)
                    current = next((item for item in fresh_frames if item["job_id"] == candidate["job_id"] and item["data"].get("media")), None)
                    blocked = any(item["data"].get("challenge") or item["data"].get("quiz") or item["data"].get("error") for item in fresh_frames)
                    if current and not blocked and not current["data"]["media"]["paused"]:
                        media, result = current["data"]["media"], {"started": True, "adopted": True}
                if not result.get("started"):
                    play_errors[key] = result.get("reason", unit["title"])
                    self._database().diagnostics.trace("learning", identifier).record("learning.play_not_started", data={"resource_id": key, "result": result})
                    await control.run(lambda: asyncio.sleep(.25 * attempts.get(key, 1))); continue
                played.add(key)
                positions[key], progress_at[key] = media["position"], now
                await self.emit(identifier, "media_started", f"{'已接管当前播放' if result.get('adopted') else '开始播放'}：{unit['title']}", {"resource_id": key, "adopted": bool(result.get("adopted")), "position": media["position"]})
            else:
                if media["position"] > positions.get(key, -1) + .1:
                    progress_at[key] = now
                    healthy_since.setdefault(key, now)
                    if now - healthy_since[key] >= 10:
                        attempts.pop(key, None); play_errors.pop(key, None)
                else: healthy_since.pop(key, None)
                if now - progress_at.get(key, now) > options.get("stall_seconds", 60):
                    await defer(key, unit["title"], "播放进度长时间没有变化", "media")
                positions[key] = media["position"]
            await control.run(lambda: asyncio.sleep(1))

    async def capture_evidence(self, identifier, browser, control):
        folder = self.settings.artifacts_root / "learning" / identifier
        folder.mkdir(parents=True, exist_ok=True)
        stream = browser.evidence()
        try:
            while True:
                try: section = await control.run(stream.__anext__)
                except StopAsyncIteration: break
                path = folder / f"section-{section['index']:03d}.png"
                path.write_bytes(section["image"])
                self.store.add_artifact(identifier, path)
        finally:
            await stream.aclose()

    async def snapshot(self, identifier):
        browser = self.browsers.get(identifier)
        if not browser: raise RuntimeError("当前任务浏览器已关闭或尚未就绪")
        return await browser.snapshot()

    async def notify(self, identifier):
        run = self.store.run(identifier)
        try:
            result = await send_completion_notification(self.settings_provider(), {
                "event": f"learning_{run['status']}", "account": run["account_name"], "course": run["course_name"],
                "result": run["error"] or "已通过平台进度复验", "status": run["status"],
            }, preferences=notification_preferences(self._database()))
            record_delivery(self._database(),result=result)
            if any(result.values()): await self.emit(identifier, "notification", "网课执行结果已推送")
        except Exception as error:
            record_delivery(self._database(),error=error)
            await self.emit(identifier, "notification_error", f"网课结果推送失败：{error}")

    async def shutdown(self):
        self.shutting_down = True
        tasks = list(self.tasks.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.store.recover()
