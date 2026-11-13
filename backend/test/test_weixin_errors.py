import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, Mock

import httpx

from app.services.notifications.delivery import send_completion_notification
from app.services.notifications import weixin as weixin_connector


class WeixinFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_keeps_update_cursor_and_uses_only_the_bound_users_token(self):
        state={"weixin_binding":{"token":"fixture","user_id":"one","context_token":"old","get_updates_buf":"cursor-1"},"weixin_delivery":{"error":"previous refusal"}}
        database=SimpleNamespace(get_setting=lambda key,default=None:state.get(key,default),set_setting=lambda key,value:state.__setitem__(key,value))
        response=httpx.Response(200,json={"ret":0,"get_updates_buf":"cursor-2","msgs":[{"from_user_id":"one","context_token":"fresh-one"},{"from_user_id":"two","context_token":"wrong-user"}]},request=httpx.Request("POST","https://fixture.test/getupdates"))
        client=SimpleNamespace(post=AsyncMock(return_value=response))
        manager=AsyncMock();manager.__aenter__.return_value=client
        with patch("app.services.notifications.weixin.httpx.AsyncClient",return_value=manager):
            result=await weixin_connector.refresh_context(database)
        self.assertTrue(result["ready"])
        self.assertEqual(client.post.call_args.kwargs["json"]["get_updates_buf"],"cursor-1")
        self.assertEqual(state["weixin_binding"]["get_updates_buf"],"cursor-2")
        self.assertEqual(state["weixin_binding"]["context_token"],"fresh-one")
        self.assertFalse(state["weixin_delivery"]["error"])

    async def test_confirmed_binding_response_does_not_expose_credentials_to_frontend(self):
        state={}
        database=SimpleNamespace(set_setting=lambda key,value:state.__setitem__(key,value))
        response=httpx.Response(200,json={"status":"confirmed","bot_token":"private-token","ilink_bot_id":"private-bot","ilink_user_id":"private-user"},request=httpx.Request("GET","https://fixture.test/status"))
        manager=AsyncMock();manager.__aenter__.return_value=SimpleNamespace(get=AsyncMock(return_value=response))
        weixin_connector.PENDING_BINDINGS["fixture-code"]={"base":"https://fixture.test","verify_code":""}
        try:
            with patch("app.services.notifications.weixin.httpx.AsyncClient",return_value=manager):
                result=await weixin_connector.check_binding(database,"fixture-code")
            self.assertEqual(result["status"],"confirmed")
            self.assertNotIn("private-",str(result))
            self.assertEqual(state["weixin_binding"]["token"],"private-token")
        finally: weixin_connector.PENDING_BINDINGS.pop("fixture-code",None)

    async def test_provider_error_preserves_code_message_and_request_id(self):
        response=httpx.Response(200,json={"ret":-2,"errmsg":"context token expired","request_id":"provider-request-17"},request=httpx.Request("POST","https://fixture.test/sendmessage"))
        client=SimpleNamespace(post=AsyncMock(return_value=response))
        manager=AsyncMock();manager.__aenter__.return_value=client
        settings=SimpleNamespace(webhook_url="",weixin_bot_token="fixture-token",weixin_user_id="fixture-user",weixin_context_token="fixture-context",weixin_base_url="https://fixture.test")
        with patch("app.services.notifications.delivery.httpx.AsyncClient",return_value=manager):
            with self.assertRaisesRegex(RuntimeError,"context token expired.*provider-request-17"):
                await send_completion_notification(settings,{"event":"assignment_needs_intervention","assignment":"fixture"})

    async def test_failed_update_response_cannot_reuse_old_token_and_report_ready(self):
        database=SimpleNamespace(get_setting=lambda *args:{"token":"fixture","user_id":"one","context_token":"old"},set_setting=Mock())
        response=httpx.Response(200,json={"ret":-2,"errmsg":"bot authorization rejected"},request=httpx.Request("POST","https://fixture.test/getupdates"))
        manager=AsyncMock();manager.__aenter__.return_value=SimpleNamespace(post=AsyncMock(return_value=response))
        with patch("app.services.notifications.weixin.httpx.AsyncClient",return_value=manager):
            with self.assertRaisesRegex(RuntimeError,"bot authorization rejected"):
                await weixin_connector.refresh_context(database)

    def test_context_token_belongs_to_the_bound_user_and_latest_message(self):
        messages={"msgs":[{"from_user_id":"one","context_token":"old-one"},{"from_user_id":"two","context_token":"other-user"},{"from_user_id":"one","context_token":"latest-one"}]}
        self.assertEqual(weixin_connector._find_context_token(messages,"one"),"latest-one")
        self.assertEqual(weixin_connector._find_context_token(messages,"missing"),"")
