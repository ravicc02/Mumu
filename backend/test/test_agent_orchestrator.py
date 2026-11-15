from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from browser_use.llm.base import BaseChatModel

from app.config import Settings
from app.db import Database
from app.events import EventHub
from app.services.agents.config import build_agent_model_config
from app.services.agents.contracts import ModelRole
from app.services.agents.gateway import ModelGateway
from app.services.agents.orchestrator import TaskOrchestrator
from app.services.learning.store import LearningStore
from app.services.tasks.runner import (
    job_browser_state,
    pause_job,
    resume_job,
    stop_job,
)


class MemorySecretStore:
    def __init__(self) -> None:
        self.values = {"model-provider/default": "fixture-key"}

    def put(self, reference, value):
        self.values[reference] = value

    def get(self, reference):
        return self.values[reference]

    def delete(self, reference):
        self.values.pop(reference, None)


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.database = Database(self.settings)
        self.database.initialize()
        LearningStore(self.database).initialize()
        self.account = self.database.create_account(
            "fixture",
            "chaoxing",
            self.settings,
        )
        self.database.update_account(self.account["id"], status="ready")
        self.database.set_setting(
            "preferences",
            {"system": {"browser_task_concurrency": 2}},
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assignment(self, name: str) -> str:
        return self.database.upsert_assignment(
            self.account["id"],
            "chaoxing",
            {
                "academicYear": 2026,
                "courseName": "fixture-course",
                "courseId": f"course-{name}",
                "classId": f"class-{name}",
                "workId": name,
                "title": name,
                "status": "unfinished",
                "submittable": True,
                "taskUrl": f"https://example.test/work/{name}",
            },
            self.settings,
        )

    async def test_global_browser_slots_limit_manual_tasks_and_keep_queue_visible(self):
        started: list[str] = []
        jobs = [
            self.database.create_job(
                self.account["id"],
                self.assignment(str(index)),
                False,
            )
            for index in range(3)
        ]
        orchestrator = TaskOrchestrator(
            lambda: self.database,
            lambda: self.settings,
            assignment_starter=lambda job_id, _settings: started.append(job_id),
            learning_starter=lambda _run_id: None,
        )

        result = await orchestrator.dispatch()
        self.assertEqual(len(started), 2)
        self.assertEqual(result["limit"], 2)
        leased = {
            lease["resource_key"]
            for lease in self.database.list_resource_leases()
        }
        self.assertIn("browser-slot:0", leased)
        self.assertIn("browser-slot:1", leased)

        waiting = [
            self.database.get_job(job["id"])
            for job in jobs
            if job["id"] not in started
        ]
        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting[0]["status"], "queued")
        self.assertEqual(waiting[0]["phase"], "waiting_resource")
        self.assertIn("浏览器任务槽位", waiting[0]["waiting_reason"])
        checkpoint = self.database.latest_task_checkpoint(waiting[0]["id"])
        self.assertEqual(checkpoint["phase"], "waiting_resource")
        self.assertIn("浏览器任务槽位", checkpoint["blockers"][0])

        started_snapshot = list(started)
        await orchestrator.dispatch()
        self.assertEqual(started, started_snapshot)

    async def test_learning_run_waits_for_the_same_global_browser_slots(self):
        store = LearningStore(self.database)
        course = store.upsert_courses(
            self.account["id"],
            [{
                "courseId": "course-one",
                "classId": "class-one",
                "courseName": "Fixture Course",
                "url": "https://example.test/course",
            }],
            2026,
        )[0]
        run = store.create_run(course["id"])
        started: list[str] = []
        orchestrator = TaskOrchestrator(
            lambda: self.database,
            lambda: self.settings,
            assignment_starter=lambda _job_id, _settings: None,
            learning_starter=started.append,
        )

        result = await orchestrator.dispatch()

        self.assertEqual(started, [run["id"]])
        self.assertEqual(result["started"], [run["id"]])
        self.assertIsNotNone(
            self.database.resource_lease(f"course:{course['id']}")
        )
        self.assertEqual(
            LearningStore(self.database).run(run["id"])["phase"],
            "starting",
        )

    async def test_manual_work_precedes_older_scheduled_work(self):
        scheduled = self.database.create_job(
            self.account["id"],
            self.assignment("scheduled"),
            False,
        )
        self.database.update_job(
            scheduled["id"],
            trigger="scheduled",
            queue_priority=10,
            created_at="2026-01-01T00:00:00+00:00",
        )
        manual = self.database.create_job(
            self.account["id"],
            self.assignment("manual"),
            False,
        )
        self.database.set_setting(
            "preferences",
            {"system": {"browser_task_concurrency": 1}},
        )
        started: list[str] = []
        orchestrator = TaskOrchestrator(
            lambda: self.database,
            lambda: self.settings,
            assignment_starter=lambda job_id, _settings: started.append(job_id),
            learning_starter=lambda _run_id: None,
        )

        await orchestrator.dispatch()

        self.assertEqual(started, [manual["id"]])
        self.assertEqual(
            self.database.get_job(scheduled["id"])["phase"],
            "waiting_resource",
        )

    async def test_waiting_assignment_can_be_paused_resumed_and_stopped_without_a_runner(self):
        job = self.database.create_job(
            self.account["id"],
            self.assignment("queue-control"),
            False,
        )
        hub = EventHub()
        pause_job(self.database, hub, job["id"])
        paused = job_browser_state(self.database.get_job(job["id"]))
        self.assertEqual(paused["status"], "paused")
        self.assertIn("resume", paused["actions"])
        self.assertIn("stop", paused["actions"])

        resume_job(self.database, hub, job["id"])
        self.assertEqual(
            self.database.get_job(job["id"])["status"],
            "queued",
        )

        stop_job(self.database, hub, job["id"])
        self.assertEqual(
            self.database.get_job(job["id"])["status"],
            "stopped",
        )

    def test_resource_leases_conflict_renew_expire_and_release(self):
        acquired, conflict = self.database.acquire_resource_leases(
            owner_id="a",
            owner_kind="assignment",
            resource_keys=["assignment:one", "browser:a"],
            ttl_seconds=30,
            now=100,
        )
        self.assertTrue(acquired)
        self.assertIsNone(conflict)

        acquired, conflict = self.database.acquire_resource_leases(
            owner_id="b",
            owner_kind="assignment",
            resource_keys=["assignment:one"],
            ttl_seconds=30,
            now=101,
        )
        self.assertFalse(acquired)
        self.assertEqual(conflict, "assignment:one")
        self.assertEqual(
            self.database.renew_resource_leases("a", ttl_seconds=40, now=110),
            2,
        )
        self.assertEqual(
            self.database.cleanup_expired_resource_leases(now=149),
            0,
        )
        self.assertEqual(
            self.database.cleanup_expired_resource_leases(now=151),
            2,
        )
        self.assertFalse(self.database.list_resource_leases())

        self.database.acquire_resource_leases(
            owner_id="c",
            owner_kind="learning",
            resource_keys=["course:one"],
            ttl_seconds=30,
            now=200,
        )
        self.assertEqual(self.database.release_resource_leases("c"), 1)

    def test_provider_circuit_opens_claims_one_probe_and_recovers(self):
        health = self.database.get_model_provider_health(
            "default",
            default_concurrency=4,
        )
        self.assertEqual(health["adaptive_concurrency"], 4)

        for offset in range(3):
            result = self.database.record_model_provider_failure(
                "default",
                "timeout",
                threshold=3,
                max_concurrency=4,
                base_cooldown=30,
                now=100 + offset,
            )
        self.assertEqual(result["state"], "open")
        opened_until = float(result["opened_until"])
        self.assertGreaterEqual(opened_until, 127)
        self.assertEqual(
            Database(self.settings)
            .get_model_provider_health("default")
            ["state"],
            "open",
        )

        allowed, state, available_at = self.database.claim_model_provider(
            "default",
            now=opened_until - 1,
        )
        self.assertFalse(allowed)
        self.assertEqual(state, "open")
        self.assertEqual(available_at, opened_until)

        allowed, state, _ = self.database.claim_model_provider(
            "default",
            now=opened_until + 1,
        )
        self.assertTrue(allowed)
        self.assertEqual(state, "half_open")
        allowed_again, state_again, _ = self.database.claim_model_provider(
            "default",
            now=opened_until + 2,
        )
        self.assertFalse(allowed_again)
        self.assertEqual(state_again, "half_open")

        failed_probe = self.database.record_model_provider_failure(
            "default",
            "upstream_5xx",
            threshold=3,
            max_concurrency=4,
            now=opened_until + 3,
        )
        self.assertEqual(failed_probe["state"], "open")
        recovered = self.database.record_model_provider_success(
            "default",
            max_concurrency=4,
            now=opened_until + 4,
        )
        self.assertEqual(recovered["state"], "closed")
        self.assertEqual(recovered["consecutive_failures"], 0)

    async def test_model_route_retries_rate_limit_then_uses_compatible_model(self):
        config = build_agent_model_config(
            provider_name="fixture",
            base_url="https://example.test/v1",
            models=["primary-model", "fallback-model"],
            reasoning_effort="high",
            secret_ref="model-provider/default",
        )
        self.database.set_setting(
            "agent_model_config",
            config.model_dump(mode="json"),
        )

        class RateLimited(RuntimeError):
            status_code = 429
            response = SimpleNamespace(headers={"Retry-After": "0.05"})

        calls: list[str] = []

        class Delegate:
            def __init__(self, model):
                self.model = model
                self.provider = "default"
                self.name = f"default/{model}"

            async def ainvoke(self, messages, output_format=None, **kwargs):
                calls.append(self.model)
                if self.model == "primary-model":
                    raise RateLimited("rate limited")
                return SimpleNamespace(usage=None, completion="ok")

        gateway = ModelGateway(
            self.database,
            self.settings,
            secret_store=MemorySecretStore(),
        )
        waiting: list[str | None] = []
        model = gateway.build_route(
            ModelRole.STRUCTURED_ANSWER,
            attempt_id="attempt-route",
            delegate_builder=lambda resolved, _key: Delegate(
                resolved.profile.model
            ),
            waiting_callback=lambda reason, _retry_at: waiting.append(reason),
            max_wait_seconds=5,
        )
        self.assertIsInstance(model, BaseChatModel)

        result = await model.ainvoke(
            [{"role": "user", "content": "private prompt"}],
        )

        self.assertEqual(result.completion, "ok")
        self.assertEqual(
            calls,
            ["primary-model", "primary-model", "fallback-model"],
        )
        self.assertTrue(any(reason and "重试" in reason for reason in waiting))
        ledger = self.database.list_model_calls(attempt_id="attempt-route")
        self.assertEqual([item["candidate_index"] for item in reversed(ledger)], [0, 0, 1])
        self.assertEqual(
            [item["status"] for item in reversed(ledger)],
            ["error", "error", "succeeded"],
        )


if __name__ == "__main__":
    unittest.main()
