from __future__ import annotations

import asyncio
import logging


logger = logging.getLogger(__name__)


async def lease_heartbeat(
    database,
    owner_id: str,
    *,
    interval_seconds: float = 15,
    ttl_seconds: float = 60,
) -> None:
    while True:
        try:
            database.renew_resource_leases(
                owner_id,
                ttl_seconds=ttl_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            if not database.path.parent.exists():
                return
            logger.exception("Resource lease heartbeat failed for %s", owner_id)
        await asyncio.sleep(max(1.0, float(interval_seconds)))
