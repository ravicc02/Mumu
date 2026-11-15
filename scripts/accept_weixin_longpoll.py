from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _state(base_url: str) -> dict:
    with urllib.request.urlopen(
        base_url.rstrip("/") + "/api/connectors/weixin",
        timeout=5,
    ) as response:
        payload = json.load(response)
    channel = payload.get("channel") or {}
    return {
        "configured": bool(payload.get("configured")),
        "ready": bool(payload.get("ready")),
        "state": channel.get("state"),
        "channel_ready": bool(channel.get("ready")),
        "context_ready": bool(channel.get("context_ready")),
        "last_poll_at": channel.get("last_poll_at"),
        "last_error": channel.get("last_error"),
    }


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def run(base_url: str, duration_seconds: int, interval_seconds: int) -> dict:
    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + max(60, duration_seconds)
    samples: list[dict] = []
    failures = 0
    consecutive_failures = 0
    previous_poll: datetime | None = None
    poll_advances = 0
    while time.monotonic() < deadline:
        sampled_at = datetime.now(timezone.utc)
        try:
            state = _state(base_url)
        except Exception as error:
            failures += 1
            consecutive_failures += 1
            state = {
                "configured": False,
                "ready": False,
                "state": "unreachable",
                "channel_ready": False,
                "context_ready": False,
                "last_poll_at": None,
                "last_error": type(error).__name__,
            }
        current_poll = _parse(state.get("last_poll_at"))
        if previous_poll and current_poll and current_poll > previous_poll:
            poll_advances += 1
        if current_poll:
            previous_poll = max(previous_poll, current_poll) if previous_poll else current_poll
        stale = bool(
            current_poll
            and sampled_at - current_poll > timedelta(seconds=90)
        )
        healthy = bool(
            state["configured"]
            and state["ready"]
            and state["state"] == "ready"
            and state["channel_ready"]
            and state["context_ready"]
            and not stale
        )
        consecutive_failures = 0 if healthy else consecutive_failures + 1
        samples.append({
            "at": sampled_at.isoformat(),
            **state,
            "stale": stale,
            "healthy": healthy,
        })
        if consecutive_failures >= 3:
            raise RuntimeError(
                "Weixin long-poll acceptance observed three consecutive unhealthy samples"
            )
        time.sleep(max(1, interval_seconds))
    finished = datetime.now(timezone.utc)
    report = {
        "schema_version": 1,
        "status": "passed",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 2),
        "sample_count": len(samples),
        "poll_advances": poll_advances,
        "failures": failures,
        "samples": samples,
    }
    if poll_advances < 2:
        raise RuntimeError("Weixin update cursor did not advance during acceptance")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Observe a live Weixin iLink channel without reading credentials."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8877")
    parser.add_argument("--duration-seconds", type=int, default=600)
    parser.add_argument("--interval-seconds", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = run(
            args.base_url,
            args.duration_seconds,
            args.interval_seconds,
        )
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    output = args.output or (
        ROOT
        / ".runtime"
        / "weixin-longpoll"
        / f"weixin-longpoll-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "duration_seconds": report["duration_seconds"],
        "sample_count": report["sample_count"],
        "poll_advances": report["poll_advances"],
        "failures": report["failures"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
