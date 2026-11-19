import os
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx2
from streamlit.testing.v1 import AppTest


_APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"
_URL = "http://backend.test"


class FrontendTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"CHAT_API_BASE_URL": _URL})
        environment.start()
        self.addCleanup(environment.stop)
        self.saved_history = [
            {"role": "user", "content": "我要准备答辩。"},
            {"role": "assistant", "content": "我们可以先练习开场。"},
        ]
        request_patch = patch("httpx2.request", side_effect=self.backend_response)
        self.request = request_patch.start()
        self.addCleanup(request_patch.stop)

    def backend_response(self, method, url, **kwargs):
        request = httpx2.Request(method, url)
        if method == "GET":
            self.assertEqual(url, f"{_URL}/history")
            self.assertEqual(kwargs["params"], {"session_id": "session-1"})
            return httpx2.Response(200, json=self.saved_history, request=request)

        self.assertEqual((method, url), ("POST", f"{_URL}/chat"))
        payload = kwargs["json"]
        reply = {"session_id": "session-1", "reply": "我们可以一起想办法。"}
        if payload["debug"]:
            reply["debug"] = {
                "emotion_agent_status": "success",
                "response_agent_status": "success",
                "emotion": {"sentiment": "negative", "emotion": "anxiety"},
            }
        return httpx2.Response(200, json=reply, request=request)

    def test_sends_next_message_with_same_session_and_renders_opt_in_debug(self):
        page = AppTest.from_file(_APP, default_timeout=12).run()
        self.assertFalse(page.exception)
        self.assertFalse(page.json)
        self.request.assert_not_called()

        page.chat_input[0].set_value("我有些紧张。 ").run()

        self.assertFalse(page.exception)
        self.assertEqual(
            self.request.call_args.kwargs["json"], {"message": "我有些紧张。 ", "debug": False}
        )
        self.assertEqual(page.query_params["session_id"], ["session-1"])
        self.assertEqual(len(page.chat_message), 2)
        self.assertFalse(page.json)

        page.toggle(key="debug_enabled").set_value(True).run()
        page.chat_input[0].set_value("有什么办法？").run()

        self.assertFalse(page.exception)
        self.assertEqual(self.request.call_count, 2)
        self.assertEqual(
            self.request.call_args.kwargs["json"],
            {"message": "有什么办法？", "session_id": "session-1", "debug": True},
        )
        self.assertEqual(len(page.chat_message), 4)
        self.assertIn("anxiety", page.json[0].value)

    def test_restores_history_from_url_then_starts_a_new_conversation(self):
        page = AppTest.from_file(_APP, default_timeout=12)
        page.query_params["session_id"] = ["session-1"]
        page.run()

        self.assertFalse(page.exception)
        self.assertEqual(len(page.chat_message), 2)
        self.assertEqual(page.session_state["messages"], self.saved_history)
        self.request.assert_called_once()

        page.button(key="new_chat").click().run()

        self.assertFalse(page.exception)
        self.assertEqual(len(page.chat_message), 0)
        self.assertNotIn("session_id", page.query_params)
        page.chat_input[0].set_value("新对话").run()
        self.assertNotIn("session_id", self.request.call_args.kwargs["json"])

    def test_failed_connection_keeps_saved_messages_and_displays_error(self):
        page = AppTest.from_file(_APP, default_timeout=12)
        page.query_params["session_id"] = ["session-1"]
        page.run()
        self.request.side_effect = httpx2.ConnectError("offline")

        page.chat_input[0].set_value("未发送成功的消息").run()

        self.assertFalse(page.exception)
        self.assertIn("连接聊天服务", page.error[0].value)
        self.assertEqual(page.session_state["messages"], self.saved_history)
        self.assertEqual(page.query_params["session_id"], ["session-1"])

    def test_restores_session_from_sidebar_form(self):
        page = AppTest.from_file(_APP, default_timeout=12).run()

        page.text_input[0].set_value("session-1")
        restore = next(button for button in page.button if button.label == "恢复会话")
        restore.click().run()

        self.assertFalse(page.exception)
        self.assertEqual(page.session_state["messages"], self.saved_history)
        self.assertEqual(page.query_params["session_id"], ["session-1"])
