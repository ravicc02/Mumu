from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe(self, job_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers[job_id].add(queue)
        return queue

    def unsubscribe(
        self,
        job_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        subscribers = self._subscribers.get(job_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(job_id, None)

    async def publish(self, job_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(job_id, set())):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"kind": "resync_required", "job_id": job_id})
            queue.put_nowait(event)


event_hub = EventHub()
