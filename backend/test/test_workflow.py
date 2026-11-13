from __future__ import annotations

import asyncio
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.db import Database
from app.events import EventHub
from app.services import agent_runner as runner
from app.services.job_state import annotate_queues, available_job_actions
from app.services.quiz_adapter import QuizAnswer, QuizAnswerSheet


def fixture(root):
    settings = Settings(data_root=Path(root), openai_api_key="test-key", openai_model="test-model")
    settings.ensure_directories()
    db = Database(settings)
    db.initialize()
    account = db.create_account("test", "chaoxing", settings)
    db.update_account(account["id"], status="ready")
    assignment = db.upsert_assignment(account["id"], "chaoxing", {
        "title": "workflow test", "taskUrl": "https://example.test/work?workId=one",
        "workId": "one", "courseId": "course", "classId": "class", "submittable": True,
    }, settings)
    job = db.create_job(account["id"], assignment, False, True)
    return settings, db, account, job


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_holds_the_result_of_an_inflight_operation(self):
        control = runner.JobControl()
        started, finished = asyncio.Event(), asyncio.Event()
        async def operation():
            started.set()
            await finished.wait()
            return 7
        task = asyncio.create_task(control.run(operation))
        await started.wait()
        control.resume_event.clear()
        control.paused_event.set()
        finished.set()
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), .03)
        control.paused_event.clear()
        control.resume_event.set()
        self.assertEqual(await task, 7)

    async def test_stop_cancels_inflight_generation(self):
        control = runner.JobControl()
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def operation():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        task = asyncio.create_task(control.run(operation))
        await started.wait()
        control.stop_requested = True
        control.stop_event.set()
        control.resume_event.set()
        with self.assertRaises(runner.JobStopped):
            await task
        self.assertTrue(cancelled.is_set())

    async def test_paused_queued_job_does_not_hold_the_account_lock(self):
        control, lock = runner.JobControl(), asyncio.Lock()
        await lock.acquire()
        task = asyncio.create_task(runner._acquire_account_lock(lock, control))
        control.resume_event.clear()
        control.paused_event.set()
        lock.release()
        await asyncio.wait_for(lock.acquire(), .1)
        self.assertFalse(task.done())
        lock.release()
        control.paused_event.clear()
        control.resume_event.set()
        await asyncio.wait_for(task, .1)
        self.assertTrue(lock.locked())
        lock.release()

    async def test_stop_while_waiting_does_not_leak_an_account_lock(self):
        control, lock = runner.JobControl(), asyncio.Lock()
        await lock.acquire()
        task = asyncio.create_task(runner._acquire_account_lock(lock, control))
        control.stop_requested = True
        control.stop_event.set()
        lock.release()
        with self.assertRaises(runner.JobStopped):
            await task
        self.assertFalse(lock.locked())

    async def test_structured_fill_pauses_between_units(self):
        with tempfile.TemporaryDirectory() as root:
            settings, db, account, job = fixture(root)
            db.transition_job(job["id"], "running")
            job = {**job, "task_url": "https://example.test/mooc2/work/task", "interactive": False}
            control = runner.JobControl()
            first, second = asyncio.Event(), asyncio.Event()
            session = type("Session", (), {"navigate_to": AsyncMock(), "get_current_page": AsyncMock(return_value=object())})()
            units = [{"unit_id": str(i), "kind": "long", "type": "简答", "fields": [{}]} for i in (1, 2)]
            sheet = QuizAnswerSheet(answers=[QuizAnswer(unit_id=str(i), values=[f"answer-{i}"]) for i in (1, 2)])
            written = []
            async def fill(page, answer_sheet):
                answer = answer_sheet.answers[0]
                written.append(answer.unit_id)
                if answer.unit_id == "2": second.set()
                return [{"unit_id": answer.unit_id, "field_values": answer.values}]
            async def emit(kind, message, data=None):
                if kind == "quiz_unit_filled" and data["filled_count"] == 1:
                    control.resume_event.clear(); control.paused_event.set(); first.set()
            with patch.object(runner, "extract_questions", AsyncMock(return_value={"question_count": 2, "unit_count": 2, "units": units})), patch.object(runner, "generate_answers", AsyncMock(return_value=sheet)), patch.object(runner, "fill_answers", fill), patch.object(runner.asyncio, "sleep", AsyncMock()):
                task = asyncio.create_task(runner._run_structured_chaoxing_quiz(db, job, session, object(), emit, control))
                await asyncio.wait_for(first.wait(), 1)
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(second.wait(), .03)
                self.assertEqual(written, ["1"])
                control.paused_event.clear(); control.resume_event.set()
                result = await task
            self.assertEqual(result["filled_count"], 2)
            self.assertEqual(db.get_job(job["id"])["completed_units"], 2)


class StateTests(unittest.TestCase):
    def test_history_and_queue_order_use_timezones_instead_of_timestamp_text(self):
        with tempfile.TemporaryDirectory() as root:
            _, db, account, first = fixture(root)
            db.transition_job(first["id"], "stopped")
            db.update_job(first["id"], created_at="2026-11-11T17:00:00+08:00")
            second = db.create_job(account["id"], first["assignment_id"], False, parent_job_id=first["id"], attempt=2)
            db.update_job(second["id"], created_at="2026-11-11T10:00:00+00:00")
            self.assertEqual(db.list_jobs()[0]["id"], second["id"])
            self.assertEqual(db.next_attempt_for_assignment(first["assignment_id"]), (second["id"], 3))
            queue = annotate_queues([
                {"id": "later", "account_id": "a", "status": "queued", "created_at": "2026-11-11T10:00:00Z"},
                {"id": "earlier", "account_id": "a", "status": "queued", "created_at": "2026-11-11T17:00:00+08:00"},
            ])
            self.assertEqual(queue[0]["queue_position"], 0)
            self.assertEqual(queue[1]["queue_position"], 0)
            self.assertTrue(all(item["blocked_by_job_id"] is None for item in queue))

    def test_stop_and_complete_cannot_both_win(self):
        with tempfile.TemporaryDirectory() as root:
            _, db, _, job = fixture(root)
            db.transition_job(job["id"], "running")
            barrier = Barrier(2)
            def transition(status):
                barrier.wait()
                try:
                    db.transition_job(job["id"], status, **({"submission_status": "submitted_verified"} if status == "completed" else {}))
                    return True
                except ValueError:
                    return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(transition, ["stopping", "completed"]))
            self.assertEqual(sum(results), 1)

    def test_actions_differ_for_review_incomplete_and_unknown_submission(self):
        base = {"status": "needs_intervention", "submission_status": "filled_pending_review", "interactive": True}
        review = available_job_actions(base, controlled=False, browser=True, retained=True)
        self.assertNotIn("continue", review)
        self.assertIn("confirm_submitted", review)
        incomplete = available_job_actions({**base, "status": "incomplete"}, controlled=False, browser=True, retained=True)
        self.assertIn("continue", incomplete)
        unknown = available_job_actions({**base, "status": "incomplete", "submission_status": "submission_unknown"}, controlled=False, browser=True, retained=True)
        self.assertNotIn("continue", unknown)
        self.assertNotIn("retry", unknown)
        self.assertIn("confirm_not_submitted", unknown)

    def test_retained_browser_does_not_block_another_task_of_the_same_account(self):
        jobs = [
            {"id": "next", "account_id": "one", "status": "queued", "created_at": "2026-01-02", "assignment_title": "next"},
            {"id": "review", "account_id": "one", "status": "needs_intervention", "browser_retained": 1, "created_at": "2026-01-01", "assignment_title": "pending review"},
        ]
        queued = annotate_queues(jobs)[0]
        self.assertIsNone(queued["blocked_by_job_id"])
        self.assertIn("独立任务浏览器", queued["waiting_reason"])

    def test_submission_evidence_must_match_the_assignment(self):
        self.assertTrue(runner._same_assignment("https://a.chaoxing.com/work?workId=one&classId=c", "https://b.chaoxing.com/result?workId=one&classId=c"))
        self.assertFalse(runner._same_assignment("https://a.chaoxing.com/work?workId=one", "https://a.chaoxing.com/result?workId=other"))
        self.assertFalse(runner._same_assignment("https://a.chaoxing.com/work?workId=one&classId=c", "https://a.chaoxing.com/result?workId=one&classId=d"))
        self.assertFalse(runner._same_assignment("https://example.test/work", "https://example.test/unrelated"))

    def test_history_review_cannot_overwrite_a_new_active_task_or_downgrade_completed(self):
        with tempfile.TemporaryDirectory() as root:
            _, db, account, job = fixture(root)
            db.transition_job(job["id"], "stopped")
            newer = db.create_job(account["id"], job["assignment_id"], False, parent_job_id=job["id"], attempt=2)
            with self.assertRaisesRegex(RuntimeError, "新任务"):
                db.update_submission_status(job["id"], "submitted_verified")
            db.transition_job(newer["id"], "stopped")
            completed = db.update_submission_status(job["id"], "submitted_verified")
            self.assertEqual(completed["status"], "completed")
            with self.assertRaisesRegex(RuntimeError, "不能修改"):
                db.update_submission_status(job["id"], "not_submitted")


class EventTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_replays_only_new_events_and_releases_idle_subscription(self):
        from app import main
        with tempfile.TemporaryDirectory() as root:
            _, db, _, job = fixture(root)
            first = db.add_event(job["id"], "queued", "first")
            second = db.add_event(job["id"], "status", "second")
            hub = EventHub()
            disconnected, sent = asyncio.Event(), asyncio.Event()
            payloads = []
            class Socket:
                async def accept(self): pass
                async def receive(self):
                    await disconnected.wait()
                    return {"type": "websocket.disconnect"}
                async def send_json(self, payload):
                    payloads.append(payload)
                    sent.set()
            with patch.object(main, "database", db), patch.object(main, "event_hub", hub):
                task = asyncio.create_task(main.job_websocket(Socket(), job["id"], after_id=first["id"]))
                await asyncio.wait_for(sent.wait(), 1)
                self.assertEqual([event["id"] for event in payloads], [second["id"]])
                disconnected.set()
                await asyncio.wait_for(task, 1)
                self.assertFalse(hub._subscribers)
                payloads.clear()
                await asyncio.wait_for(main.job_websocket(Socket(), job["id"], after_id=second["id"]), 1)
                self.assertFalse(payloads)
                self.assertFalse(hub._subscribers)

    async def test_slow_subscriber_is_bounded_and_receives_a_resync_marker(self):
        hub = EventHub()
        queue = hub.subscribe("job")
        for number in range(300):
            await hub.publish("job", {"id": number, "kind": "step"})
        self.assertLessEqual(queue.qsize(), 256)
        events = []
        while not queue.empty(): events.append(queue.get_nowait())
        self.assertTrue(any(event["kind"] == "resync_required" for event in events))
        self.assertEqual(events[-1]["id"], 299)


class RetainedWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await runner.close_preserved_sessions()

    async def test_manual_continue_keeps_the_same_browser_then_review_releases_it(self):
        with tempfile.TemporaryDirectory() as root:
            settings, db, account, job = fixture(root)
            db.transition_job(job["id"], "running")
            db.transition_job(job["id"], "incomplete", submission_status="partial_pending_review", browser_retained=1)
            lock = asyncio.Lock()
            await lock.acquire()
            session = type("Session", (), {"kill": AsyncMock(), "get_current_page_url": AsyncMock(return_value=job["task_url"])})()
            preserved = runner.PreservedSession(session, None, lock)
            runner._intervention_sessions[job["id"]] = preserved
            runner._job_sessions[job["id"]] = session
            hub = EventHub()
            with patch.object(runner, "start_job") as start:
                continued = await runner.continue_job_intervention(db, hub, settings, job["id"])
            self.assertEqual(continued["status"], "running")
            self.assertIs(start.call_args.kwargs["reused_session"], preserved)
            captured = {}

            class History:
                def final_result(self): return "全部题目已经填写，等待审核"
                def is_successful(self): return True
                def model_dump(self, **kwargs): return {"result": "filled"}

            class Agent:
                def __init__(self, **kwargs): captured.update(kwargs)
                async def run(self, max_steps, on_step_start):
                    await on_step_start(self)
                    return History()

            with patch.object(runner, "Agent", Agent), patch.object(runner, "_build_agent_llm", return_value=object()), patch.object(runner, "save_assignment_draft", AsyncMock(return_value={"saved": True})), patch.object(runner, "_capture_screenshot", AsyncMock(return_value=(b"png", job["task_url"]))), patch.object(runner, "_launch_chrome", AsyncMock(side_effect=AssertionError("must reuse browser"))) as launch, patch.object(runner, "_run_structured_chaoxing_quiz", AsyncMock()) as structured, patch.object(runner, "send_completion_notification", AsyncMock(return_value={})):
                await runner.run_job(db, hub, settings, job["id"], reused_session=preserved)
                launch.assert_not_awaited()
                structured.assert_not_awaited()
                self.assertFalse(captured["directly_open_url"])
                self.assertIn("不要重新打开作业", captured["task"])
                self.assertEqual(db.get_job(job["id"])["status"], "needs_intervention")
                self.assertTrue(lock.locked())
                reviewed = await runner.review_job_submission(db, hub, job["id"], "filled_verified")
                self.assertEqual(reviewed["status"], "needs_intervention")
                self.assertTrue(lock.locked())
                completed = await runner.review_job_submission(db, hub, job["id"], "submitted_verified")
            self.assertEqual(completed["status"], "completed")
            self.assertFalse(lock.locked())
            self.assertFalse(db.has_active_job(account["id"]))
            session.kill.assert_awaited_once()

    async def test_retry_closes_only_its_own_preserved_browser(self):
        with tempfile.TemporaryDirectory() as root:
            _, db, account, job = fixture(root)
            db.transition_job(job["id"], "running")
            db.transition_job(job["id"], "incomplete", submission_status="partial_pending_review", browser_retained=1)
            lock = asyncio.Lock()
            await lock.acquire()
            session = type("Session", (), {"kill": AsyncMock()})()
            runner._intervention_sessions[job["id"]] = runner.PreservedSession(session, None, lock)
            runner._job_sessions[job["id"]] = session
            await runner.prepare_job_retry(db, job["id"])
            self.assertFalse(lock.locked())
            self.assertEqual(db.get_job(job["id"])["status"], "incomplete")
            self.assertEqual(db.get_job(job["id"])["browser_retained"], 0)
            retry = db.create_job(account["id"], job["assignment_id"], False, parent_job_id=job["id"], attempt=2)
            self.assertEqual(retry["attempt"], 2)
            self.assertTrue(db.has_active_job(account["id"]))


class ApiWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_task_and_retry_cannot_bypass_submission_review(self):
        import httpx
        from app import main
        with tempfile.TemporaryDirectory() as root:
            settings, db, account, job = fixture(root)
            db.transition_job(job["id"], "interrupted", submission_status="submission_unknown")
            with patch.object(main, "database", db), patch.object(main, "effective_settings", return_value=settings), patch.object(main, "start_job") as start:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://workflow.test") as client:
                    for url, body in [("/api/jobs", {"assignment_id": job["assignment_id"]}), (f"/api/jobs/{job['id']}/retry", {})]:
                        response = await client.post(url, json=body)
                        self.assertEqual(response.status_code, 409, response.text)
                        self.assertIn("提交状态", response.json()["detail"])
                    db.update_submission_status(job["id"], "not_submitted")
                    newer = db.create_job(account["id"], job["assignment_id"], False, parent_job_id=job["id"], attempt=2)
                    db.transition_job(newer["id"], "stopped")
                    db.update_submission_status(job["id"], "submitted_verified")
                    for url, body in [("/api/jobs", {"assignment_id": job["assignment_id"]}), (f"/api/jobs/{newer['id']}/retry", {})]:
                        response = await client.post(url, json=body)
                        self.assertEqual(response.status_code, 409, response.text)
                        self.assertIn("完成并提交", response.json()["detail"])
                start.assert_not_called()

    async def test_collection_excludes_authorization_preview_and_task_start(self):
        import httpx
        from app import main
        with tempfile.TemporaryDirectory() as root:
            settings, db, account, job = fixture(root)
            db.transition_job(job["id"], "stopped")
            lock = asyncio.Lock()
            await lock.acquire()
            with patch.object(main, "database", db), patch.object(main, "effective_settings", return_value=settings), patch.object(main, "collection_lock", return_value=lock), patch.object(main, "start_job") as start:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://workflow.test") as client:
                    for method, url, body in [
                        ("GET", f"/api/accounts/{account['id']}/preview", None),
                        ("POST", f"/api/accounts/{account['id']}/login", None),
                        ("POST", f"/api/accounts/{account['id']}/collect", None),
                        ("POST", "/api/jobs", {"assignment_id": job["assignment_id"]}),
                        ("POST", f"/api/jobs/{job['id']}/retry", {}),
                    ]:
                        with self.subTest(url=url):
                            response = await client.request(method, url, json=body)
                            self.assertEqual(response.status_code, 409, response.text)
                            self.assertIn("采集", response.json()["detail"])
                start.assert_not_called()
            lock.release()

    async def test_authorization_excludes_profile_preview_and_task_start(self):
        import httpx
        from app import main
        with tempfile.TemporaryDirectory() as root:
            settings, db, account, job = fixture(root)
            db.transition_job(job["id"], "stopped")
            with patch.object(main, "database", db), patch.object(main, "effective_settings", return_value=settings), patch.object(main.chaoxing, "login_in_progress", return_value=True), patch.object(main.preview_manager, "screenshot", AsyncMock()) as screenshot, patch.object(main, "start_job") as start:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://workflow.test") as client:
                    requests = [
                        ("GET", f"/api/accounts/{account['id']}/preview", None),
                        ("POST", f"/api/accounts/{account['id']}/open-browser", None),
                        ("POST", f"/api/accounts/{account['id']}/collect", None),
                        ("POST", "/api/jobs", {"assignment_id": job["assignment_id"]}),
                        ("POST", f"/api/jobs/{job['id']}/retry", {}),
                    ]
                    for method, url, body in requests:
                        with self.subTest(url=url):
                            response = await client.request(method, url, json=body)
                            self.assertEqual(response.status_code, 409, response.text)
                            self.assertIn("授权", response.json()["detail"])
                screenshot.assert_not_awaited()
                start.assert_not_called()

    async def test_login_releases_preview_before_opening_authorization_browser(self):
        import httpx
        from app import main
        with tempfile.TemporaryDirectory() as root:
            settings, db, account, job = fixture(root)
            db.transition_job(job["id"], "stopped")
            order = []
            async def invalidate(account_id):
                order.append(("preview_closed", account_id))
            class Platform:
                def start_login(self, database, account_id, config):
                    order.append(("login_started", account_id))
                    return {"status": "waiting"}
            with patch.object(main, "database", db), patch.object(main.chaoxing, "login_in_progress", return_value=False), patch.object(main.preview_manager, "invalidate", side_effect=invalidate), patch.object(main.platform_registry, "get", return_value=Platform()):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://workflow.test") as client:
                    response = await client.post(f"/api/accounts/{account['id']}/login")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(order, [("preview_closed", account["id"]), ("login_started", account["id"])])

    async def test_create_pause_resume_stop_retry_and_duplicate_guard_via_http(self):
        import httpx
        from app import main
        with tempfile.TemporaryDirectory() as root:
            settings, db, account, original = fixture(root)
            db.transition_job(original["id"], "stopped")
            created = []
            def fake_start(database, hub, config, job_id):
                created.append(job_id)
                runner._job_controls[job_id] = runner.JobControl()
            with patch.object(main, "database", db), patch.object(main, "effective_settings", return_value=settings), patch.object(main, "start_job", fake_start):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://workflow.test") as client:
                    response = await client.post("/api/jobs", json={"assignment_id": original["assignment_id"], "auto_submit": False, "interactive": True})
                    self.assertEqual(response.status_code, 200, response.text)
                    job = response.json()
                    self.assertIn("pause", job["actions"])
                    self.assertEqual(job["queue_position"], 0)
                    duplicate = await client.post("/api/jobs", json={"assignment_id": original["assignment_id"]})
                    self.assertEqual(duplicate.status_code, 409)
                    paused = await client.post(f"/api/jobs/{job['id']}/pause")
                    self.assertEqual(paused.json()["status"], "paused")
                    self.assertIn("resume", paused.json()["actions"])
                    resumed = await client.post(f"/api/jobs/{job['id']}/resume")
                    self.assertEqual(resumed.json()["status"], "queued")
                    stopped = await client.post(f"/api/jobs/{job['id']}/stop")
                    self.assertEqual(stopped.json()["status"], "stopping")
                    db.transition_job(job["id"], "stopped")
                    retry = await client.post(f"/api/jobs/{job['id']}/retry", json={"auto_submit": False})
                    self.assertEqual(retry.status_code, 200, retry.text)
                    self.assertEqual(retry.json()["attempt"], 3)
                    self.assertEqual(retry.json()["auto_submit"], 0)
            for job_id in created: runner._job_controls.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
