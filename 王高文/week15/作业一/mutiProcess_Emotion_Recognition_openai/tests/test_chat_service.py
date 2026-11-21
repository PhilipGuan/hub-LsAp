import unittest
from unittest.mock import patch
from uuid import UUID

from backend.agents.emotion_agent import EmotionAPIError, EmotionOutputError
from backend.agents.response_agent import ResponseAPIError
from backend.models.schemas import ChatRequest, EmotionResult
from backend.services.chat_service import chat_service


class ChatServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        emotion_patch = patch("backend.services.chat_service.detect_emotion")
        response_patch = patch("backend.services.chat_service.generate_response")
        self.detect = emotion_patch.start()
        self.respond = response_patch.start()
        self.addCleanup(emotion_patch.stop)
        self.addCleanup(response_patch.stop)
        self.emotion = EmotionResult(sentiment="negative", emotion="anxiety")
        self.detect.return_value = self.emotion
        self.respond.return_value = "要不要先一起梳理开场？"

    async def test_passes_current_emotion_and_history_to_response_agent(self):
        request = ChatRequest(message="明天答辩，我有点紧张。", session_id="session-1", debug=True)
        history = [{"role": "user", "content": "我正在准备答辩。"}]

        result = await chat_service(request, history)

        self.detect.assert_awaited_once_with(request.message)
        self.respond.assert_awaited_once_with(request.message, self.emotion, history)
        self.assertEqual(result.session_id, "session-1")
        self.assertEqual(result.reply, "要不要先一起梳理开场？")
        self.assertEqual(result.debug.emotion_agent_status, "success")
        self.assertEqual(result.debug.response_agent_status, "success")
        self.assertEqual(result.debug.emotion, self.emotion)

    async def test_allocates_new_session_id_and_disables_debug_by_default(self):
        result = await chat_service(ChatRequest(message="你好。"))

        self.assertEqual(UUID(result.session_id).version, 4)
        self.assertIsNone(result.debug)

    async def test_retries_invalid_emotion_once_and_uses_corrected_result(self):
        self.detect.side_effect = [EmotionOutputError("invalid"), self.emotion]
        request = ChatRequest(message="我有点紧张。", debug=True)

        result = await chat_service(request)

        self.assertEqual(self.detect.await_count, 2)
        self.respond.assert_awaited_once_with(request.message, self.emotion, ())
        self.assertEqual(result.debug.emotion_agent_status, "success")

    async def test_repeated_invalid_emotion_uses_general_reply_without_agent_2(self):
        self.detect.side_effect = EmotionOutputError("invalid")

        with self.assertLogs("backend.services.chat_service", level="WARNING"):
            result = await chat_service(ChatRequest(message="我有点紧张。", debug=True))

        self.assertEqual(self.detect.await_count, 2)
        self.respond.assert_not_awaited()
        self.assertIn("愿意", result.reply)
        self.assertEqual(result.debug.emotion_agent_status, "failed")
        self.assertEqual(result.debug.response_agent_status, "skipped")
        self.assertIsNone(result.debug.emotion)

    async def test_recognition_api_failure_returns_retry_message_without_resubmitting(self):
        self.detect.side_effect = EmotionAPIError("internal provider details")

        with self.assertLogs("backend.services.chat_service", level="WARNING"):
            result = await chat_service(ChatRequest(message="你好。", debug=True))

        self.detect.assert_awaited_once()
        self.respond.assert_not_awaited()
        self.assertIn("稍后再试", result.reply)
        self.assertNotIn("internal provider details", result.reply)
        self.assertEqual(result.debug.emotion_agent_status, "failed")
        self.assertEqual(result.debug.response_agent_status, "skipped")

    async def test_response_failure_preserves_detection_result_in_debug(self):
        self.respond.side_effect = ResponseAPIError("rate limit")

        with self.assertLogs("backend.services.chat_service", level="WARNING"):
            result = await chat_service(ChatRequest(message="你好。", debug=True))

        self.assertIn("稍后再试", result.reply)
        self.assertEqual(result.debug.emotion_agent_status, "success")
        self.assertEqual(result.debug.response_agent_status, "failed")
        self.assertEqual(result.debug.emotion, self.emotion)

    async def test_explicit_self_harm_messages_use_safety_reply_without_model_calls(self):
        self.detect.side_effect = EmotionAPIError("provider unavailable")
        cases = [
            ("我现在想伤害自己。", "当地急救服务"),
            ("不想活了。", "当地急救服务"),
            ("I'm going to hurt myself.", "local emergency services"),
            ("I don't want to live anymore.", "local emergency services"),
        ]
        for message, expected_text in cases:
            with self.subTest(message=message):
                result = await chat_service(ChatRequest(message=message, debug=True))
                self.assertIn(expected_text, result.reply)
                self.assertEqual(result.debug.emotion_agent_status, "skipped")
                self.assertEqual(result.debug.response_agent_status, "skipped")
                self.assertIsNone(result.debug.emotion)
        self.detect.assert_not_awaited()
        self.respond.assert_not_awaited()

    async def test_ordinary_discussion_and_negation_use_normal_agent_flow(self):
        cases = (
            "我要写一份关于自杀预防的报告。",
            "我不想自杀，只是最近很累。",
            "I don't want to live in this city anymore.",
        )
        for message in cases:
            with self.subTest(message=message):
                result = await chat_service(ChatRequest(message=message))
                self.assertEqual(result.reply, self.respond.return_value)
        self.assertEqual(self.detect.await_count, len(cases))
