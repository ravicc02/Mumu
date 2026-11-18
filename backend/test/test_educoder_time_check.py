from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.educoder.client import EduCoderClient
from app.services.educoder.contracts import ContractError


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.ok = status < 400

    async def json(self):
        return self.payload


def client_returning(payload, status=200):
    client = EduCoderClient({})
    client.observed_at = time.monotonic()
    client.headers = {}
    client.context = SimpleNamespace(
        request=SimpleNamespace(fetch=AsyncMock(return_value=FakeResponse(payload, status)))
    )
    return client


class TimeMismatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_time_mismatch_surfaces_actionable_error_instead_of_empty_payload(self):
        # 平台连续返回 -102 时，不能把它原样交给调用方，否则会被误报成「关卡列表为空」。
        # 刷新签名头后仍被拒绝，才报可行动的校准提示。
        client = client_returning({"status": -102, "message": "服务器时间与您的设备时间不匹配"})
        client._refresh_headers = AsyncMock()
        with self.assertRaises(ContractError) as failure:
            await client.request("/api/myshixuns/x/challenges.json")
        self.assertIn("时间不匹配", str(failure.exception))
        client._refresh_headers.assert_awaited_once()

    async def test_time_mismatch_retries_once_with_fresh_headers(self):
        # -102 多半是签名时间戳过期而非真时钟偏差：刷新一次后应直接成功，
        # 不该让整份作业停在第 8 关。
        client = EduCoderClient({})
        client.observed_at = time.monotonic()
        client.headers = {}
        client._refresh_headers = AsyncMock()
        responses = [FakeResponse({"status": -102}), FakeResponse({"stages": [{"identifier": "a"}]})]

        async def fetch(*args, **kwargs):
            return responses.pop(0)

        client.context = SimpleNamespace(request=SimpleNamespace(fetch=fetch))
        result = await client.request("/api/myshixuns/x/challenges.json")
        self.assertIn("stages", result)
        client._refresh_headers.assert_awaited_once()

    async def test_pending_evaluation_status_still_passes_through(self):
        # game_status.json 的 -1 表示评测未完成，是正常等待状态，不能当作拒绝。
        client = client_returning({"status": -1, "message": "pending"})
        result = await client.request("/api/game_status.json")
        self.assertEqual(result["status"], -1)

    async def test_negative_status_outside_pending_still_rejected(self):
        client = client_returning({"status": -1, "message": "拒绝"})
        with self.assertRaises(ContractError):
            await client.request("/api/myshixuns/x/challenges.json")


if __name__ == "__main__":
    unittest.main()
