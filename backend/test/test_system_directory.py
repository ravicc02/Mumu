from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app import main
from app.config import Settings


class LogDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_root=Path(self.temp.name))
        self.app = FastAPI()
        self.app.post("/api/system/open-log-directory")(main.open_log_directory)
        override = patch.object(main, "settings", self.settings)
        override.start()
        self.addCleanup(override.stop)

    async def post(self, origin="http://127.0.0.1:5174", host="127.0.0.1", **kwargs):
        transport = httpx.ASGITransport(app=self.app, client=(host, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8877") as client:
            return await client.post("/api/system/open-log-directory", headers={"Origin": origin} if origin else {}, **kwargs)

    async def test_only_the_configured_log_directory_is_opened(self):
        with patch.object(main.os, "startfile", create=True) as opener:
            response = await self.post(json={"path": "C:/unrelated"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"opened": True})
        opener.assert_called_once_with(str(self.settings.logs_root))
        self.assertTrue(self.settings.logs_root.is_dir())

    async def test_remote_or_untrusted_origins_never_open_a_folder(self):
        for origin, host in (("https://evil.example", "127.0.0.1"), (None, "127.0.0.1"),
            ("null", "127.0.0.1"), ("http://127.0.0.1:5174", "192.168.1.20")):
            with self.subTest(origin=origin, host=host), patch.object(main.os, "startfile", create=True) as opener:
                self.assertEqual((await self.post(origin, host)).status_code, 403)
                opener.assert_not_called()

    async def test_os_failure_returns_an_actionable_error_without_exposing_paths(self):
        with patch.object(main.os, "startfile", side_effect=OSError("private system detail"), create=True), self.assertLogs(main.logger, level="ERROR"):
            response = await self.post()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private", response.text)
        self.assertIn("文件管理器", response.json()["detail"])

    async def test_unsupported_system_does_not_claim_success(self):
        with patch.object(main.os, "startfile", None, create=True):
            self.assertEqual((await self.post()).status_code, 501)
