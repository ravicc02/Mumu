from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psutil
from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.services.agents.contracts import ModelRole, ProviderProfile  # noqa: E402
from app.services.agents.quota import ModelQuotaManager  # noqa: E402


def _release(manager: ModelQuotaManager, lease) -> None:
    lease.release()
    manager.release_task_lock()


def _browser_rss_mb(marker: str) -> float:
    total = 0
    for process in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            command = " ".join(process.info.get("cmdline") or [])
            if marker not in command:
                continue
            memory = process.info.get("memory_info")
            if memory:
                total += int(memory.rss)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return round(total / (1024 * 1024), 2)


async def _browser_capacity(
    *,
    tasks: int,
    slots: int,
    hold_seconds: float,
) -> dict:
    run_id = uuid.uuid4().hex
    in_flight = 0
    peak_in_flight = 0
    slot_violations = 0
    completed = 0
    observations: list[dict] = []
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(slots)

    async with async_playwright() as playwright:
        async def worker(index: int) -> None:
            nonlocal in_flight, peak_in_flight, slot_violations, completed
            marker = f"--mumu-capacity-run={run_id}"
            async with semaphore:
                async with lock:
                    in_flight += 1
                    peak_in_flight = max(peak_in_flight, in_flight)
                    if in_flight > slots:
                        slot_violations += 1
                started = time.perf_counter()
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=[marker],
                )
                try:
                    page = await browser.new_page()
                    await page.set_content(
                        "<main><h1>Mumu capacity fixture</h1></main>"
                    )
                    heading = await page.locator("h1").inner_text()
                    if heading != "Mumu capacity fixture":
                        raise RuntimeError("Browser capacity fixture did not render")
                    await asyncio.sleep(hold_seconds)
                    rss_mb = await asyncio.to_thread(_browser_rss_mb, marker)
                    observations.append({
                        "task": index,
                        "rss_mb": rss_mb,
                        "duration_ms": round(
                            (time.perf_counter() - started) * 1000,
                            1,
                        ),
                    })
                finally:
                    await browser.close()
                    async with lock:
                        completed += 1
                        in_flight -= 1

        await asyncio.gather(*(worker(index) for index in range(tasks)))

    return {
        "tasks": tasks,
        "slots": slots,
        "completed": completed,
        "peak_in_flight": peak_in_flight,
        "slot_violations": slot_violations,
        "peak_single_browser_rss_mb": max(
            (item["rss_mb"] for item in observations),
            default=0,
        ),
        "observations": observations,
    }


async def _quota_reserve() -> dict:
    with tempfile.TemporaryDirectory(prefix="mumu-capacity-") as temp:
        settings = Settings(data_root=Path(temp))
        settings.ensure_directories()
        database = Database(settings)
        database.initialize()
        provider = ProviderProfile(
            id="capacity",
            name="Capacity Fixture",
            base_url="https://example.test/v1",
            max_concurrency=2,
            requests_per_minute=120,
            tokens_per_minute=0,
            queue_limit=120,
        )
        loop = asyncio.get_running_loop()
        background = ModelQuotaManager(
            database,
            attempt_id="capacity-background-1",
            role=ModelRole.STRUCTURED_ANSWER,
        )
        assistant = ModelQuotaManager(
            database,
            attempt_id="capacity-assistant",
            role=ModelRole.ASSISTANT,
        )
        blocked_background = ModelQuotaManager(
            database,
            attempt_id="capacity-background-2",
            role=ModelRole.CODE,
        )
        background_lease = await background.acquire(
            provider,
            estimated_tokens=200,
            deadline=loop.time() + 5,
        )
        blocked = None
        assistant_lease = None
        background_lease_2 = None
        try:
            started = time.perf_counter()
            assistant_lease = await assistant.acquire(
                provider,
                estimated_tokens=200,
                deadline=loop.time() + 2,
            )
            assistant_wait_ms = round((time.perf_counter() - started) * 1000, 1)
            blocked = asyncio.create_task(
                blocked_background.acquire(
                    provider,
                    estimated_tokens=200,
                    deadline=loop.time() + 5,
                )
            )
            await asyncio.sleep(0.25)
            background_was_blocked = not blocked.done()
            _release(background, background_lease)
            background_lease = None
            background_lease_2 = await asyncio.wait_for(blocked, timeout=3)
            _release(blocked_background, background_lease_2)
            background_lease_2 = None
        finally:
            if blocked is not None and not blocked.done():
                blocked.cancel()
                await asyncio.gather(blocked, return_exceptions=True)
            if assistant_lease is not None:
                _release(assistant, assistant_lease)
            if background_lease_2 is not None:
                _release(blocked_background, background_lease_2)
            if background_lease is not None:
                _release(background, background_lease)

    return {
        "provider_limit": 2,
        "background_slots": 1,
        "assistant_wait_ms": assistant_wait_ms,
        "assistant_reserve_working": assistant_wait_ms < 1000,
        "second_background_blocked": background_was_blocked,
    }


async def run(tasks: int, slots: int, hold_seconds: float) -> dict:
    started = time.perf_counter()
    browser = await _browser_capacity(
        tasks=tasks,
        slots=slots,
        hold_seconds=hold_seconds,
    )
    quota = await _quota_reserve()
    memory = psutil.virtual_memory()
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "passed"
            if browser["slot_violations"] == 0
            and browser["completed"] == tasks
            and quota["assistant_reserve_working"]
            and quota["second_background_blocked"]
            else "failed"
        ),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "memory_total_mb": round(memory.total / (1024 * 1024), 1),
        },
        "browser_capacity": browser,
        "model_quota": quota,
        "duration_seconds": round(time.perf_counter() - started, 2),
    }
    if report["status"] != "passed":
        raise RuntimeError(json.dumps(report, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run isolated Mumu Agent browser and quota capacity acceptance."
    )
    parser.add_argument("--tasks", type=int, default=8)
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--hold-seconds", type=float, default=0.75)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.tasks < 1 or args.slots < 1:
        raise SystemExit("--tasks and --slots must be positive")
    report = asyncio.run(
        run(
            tasks=min(args.tasks, 64),
            slots=min(args.slots, 16),
            hold_seconds=max(0.1, min(args.hold_seconds, 30)),
        )
    )
    output = args.output or (
        ROOT
        / ".runtime"
        / "agent-capacity"
        / f"agent-capacity-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "browser": {
            key: report["browser_capacity"][key]
            for key in (
                "tasks",
                "slots",
                "completed",
                "peak_in_flight",
                "slot_violations",
                "peak_single_browser_rss_mb",
            )
        },
        "model_quota": report["model_quota"],
        "duration_seconds": report["duration_seconds"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
