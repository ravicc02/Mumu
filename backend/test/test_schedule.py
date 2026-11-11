from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.main import collection_due


class CollectionScheduleTests(unittest.TestCase):
    def test_disabled_or_not_ready_schedule_is_not_due(self) -> None:
        self.assertFalse(
            collection_due({
                "auto_collect_enabled": 0,
                "auto_collect_interval_minutes": 60,
                "last_collection_at": None,
            })
        )

    def test_missing_or_stale_last_collection_is_due(self) -> None:
        self.assertTrue(
            collection_due({
                "auto_collect_enabled": 1,
                "auto_collect_interval_minutes": 60,
                "last_collection_at": None,
            })
        )
        stale = (
            datetime.now(timezone.utc) - timedelta(minutes=61)
        ).isoformat()
        self.assertTrue(
            collection_due({
                "auto_collect_enabled": 1,
                "auto_collect_interval_minutes": 60,
                "last_collection_at": stale,
            })
        )

    def test_recent_collection_is_not_due(self) -> None:
        recent = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
        ).isoformat()
        self.assertFalse(
            collection_due({
                "auto_collect_enabled": 1,
                "auto_collect_interval_minutes": 60,
                "last_collection_at": recent,
            })
        )


if __name__ == "__main__":
    unittest.main()
