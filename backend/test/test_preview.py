from __future__ import annotations

import unittest

from app.services.preview import ProfilePreviewManager


class FakePage:
    def __init__(self, closed: bool) -> None:
        self.closed = closed

    def is_closed(self) -> bool:
        return self.closed


class FakeContext:
    pass


class PreviewManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_interactive_page_is_released(self) -> None:
        manager = ProfilePreviewManager()
        page = FakePage(closed=True)
        context = FakeContext()
        manager._interactive_accounts.add("account-1")
        manager._pages["account-1"] = page
        manager._contexts["account-1"] = context

        self.assertFalse(await manager.is_interactive("account-1"))
        self.assertNotIn("account-1", manager._interactive_accounts)
        self.assertNotIn("account-1", manager._pages)
        self.assertNotIn("account-1", manager._contexts)

    async def test_open_interactive_page_remains_active(self) -> None:
        manager = ProfilePreviewManager()
        page = FakePage(closed=False)
        context = FakeContext()
        manager._interactive_accounts.add("account-1")
        manager._pages["account-1"] = page
        manager._contexts["account-1"] = context

        self.assertTrue(await manager.is_interactive("account-1"))
        self.assertIn("account-1", manager._interactive_accounts)


if __name__ == "__main__":
    unittest.main()
