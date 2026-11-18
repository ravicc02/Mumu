from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.runtime.profile_cache import prune_accounts, prune_browser_cache


class ProfileCacheTests(unittest.TestCase):
    def _profile(self, root: Path) -> Path:
        profile = root / "accounts" / ("a" * 32) / "browser"
        (profile / "Default" / "Cache").mkdir(parents=True)
        (profile / "Default" / "Cache" / "blob").write_bytes(b"x" * 2000)
        (profile / "Default" / "Code Cache" / "js").mkdir(parents=True)
        (profile / "Default" / "Code Cache" / "js" / "a").write_bytes(b"y" * 500)
        (profile / "Default" / "Network").mkdir(parents=True)
        (profile / "Default" / "Network" / "Cookies").write_bytes(b"credential")
        (profile / "Default" / "Local Storage" / "leveldb").mkdir(parents=True)
        (profile / "Default" / "Local Storage" / "leveldb" / "000003.log").write_text("token")
        (profile / "Default" / "Preferences").write_text("{}")
        (profile / "Default" / "History").write_bytes(b"h" * 300)
        (profile / "storage-state.json").write_text('{"cookies":[]}')
        (profile / "avatar.bin").write_bytes(b"avatar")
        (profile / "optimization_guide_model_store").mkdir(parents=True)
        (profile / "optimization_guide_model_store" / "model").write_bytes(b"m" * 800)
        (profile / "BrowserMetrics-spare.pma").write_bytes(b"p" * 100)
        return profile

    def test_prune_removes_regenerable_cache_and_keeps_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = self._profile(Path(directory))

            result = prune_browser_cache(profile)

            self.assertGreaterEqual(result["freed"], 3700)
            self.assertFalse((profile / "Default" / "Cache").exists())
            self.assertFalse((profile / "Default" / "Code Cache").exists())
            self.assertFalse((profile / "Default" / "History").exists())
            self.assertFalse((profile / "optimization_guide_model_store").exists())
            self.assertFalse((profile / "BrowserMetrics-spare.pma").exists())
            self.assertEqual((profile / "Default" / "Network" / "Cookies").read_bytes(), b"credential")
            self.assertTrue((profile / "Default" / "Local Storage" / "leveldb" / "000003.log").is_file())
            self.assertTrue((profile / "Default" / "Preferences").is_file())
            self.assertTrue((profile / "storage-state.json").is_file())
            self.assertTrue((profile / "avatar.bin").is_file())

    def test_missing_directory_is_harmless(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "accounts" / "nope" / "browser"
            self.assertEqual(prune_browser_cache(missing), {"freed": 0})
            self.assertEqual(prune_accounts(Path(directory) / "accounts"), {"accounts": 0, "freed": 0})

    def test_accounts_are_pruned_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "accounts"
            first = self._profile(Path(directory))
            second = root / ("b" * 32) / "browser"
            (second / "Default" / "Cache").mkdir(parents=True)
            (second / "Default" / "Cache" / "blob").write_bytes(b"z" * 100)
            (second / "Default" / "Network").mkdir(parents=True)
            (second / "Default" / "Network" / "Cookies").write_bytes(b"credential")

            result = prune_accounts(root)

            self.assertEqual(result["accounts"], 2)
            self.assertGreater(result["freed"], 0)
            self.assertFalse((first / "Default" / "Cache").exists())
            self.assertFalse((second / "Default" / "Cache").exists())
            self.assertTrue((second / "Default" / "Network" / "Cookies").is_file())


if __name__ == "__main__":
    unittest.main()
