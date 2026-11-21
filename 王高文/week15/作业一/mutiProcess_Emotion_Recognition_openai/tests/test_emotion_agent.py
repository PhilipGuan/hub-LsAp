import json
import os
import unittest
from unittest.mock import patch

import httpx2
from openai import AsyncOpenAI

from backend.agents.emotion_agent import (
    EmotionAPIError,
    EmotionConfigurationError,
    EmotionOutputError,
    detect_emotion,
)
from backend.models.schemas import EmotionResult


def completion(content, *, refusal=None):
    return httpx2.Response(
        200,
        json={
            "id": "test-completion",
            "object": "chat.completion",
            "created": 1,
            "model": "mimo-v2.6-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content, "refusal": refusal},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def api_error(status, message):
    return httpx2.Response(status, json={"error": {"message": message}})


class EmotionAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environment = patch.dict(
            os.environ,
            {
                "MIMO_API_KEY": "test-key",
                "MIMO_BASE_URL": "https://mimo.test/v1",
                "EMOTION_MODEL": "mimo-v2.6-flash",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.responses = []
        self.requests = []
        client = patch("backend.agents.emotion_agent.AsyncOpenAI", side_effect=self.make_client)
        client.start()
        self.addCleanup(client.stop)

    def make_client(self, **kwargs):
        transport = httpx2.MockTransport(self.handle_request)
        return AsyncOpenAI(http_client=httpx2.AsyncClient(transport=transport), **kwargs)

    def handle_request(self, request):
        self.assertEqual(str(request.url), "https://mimo.test/v1/chat/completions")
        self.requests.append(json.loads(request.content))
        self.assertTrue(self.responses, "Unexpected request to MiMo")
        return self.responses.pop(0)

    async def test_three_sentiments_use_non_streaming_structured_output(self):
        cases = [
            ("我拿到奖学金了！", "positive", "joy"),
            ("我很担心明天的考试。", "negative", "anxiety"),
            ("现在是下午三点。", "neutral", "neutral"),
        ]
        for message, sentiment, emotion in cases:
            with self.subTest(sentiment=sentiment):
                payload = json.dumps({"sentiment": sentiment, "emotion": emotion})
                self.responses.append(completion(payload))
                result = await detect_emotion(message)
                self.assertIsInstance(result, EmotionResult)
                self.assertEqual((result.sentiment, result.emotion), (sentiment, emotion))
                request = self.requests[-1]
                self.assertEqual(request["model"], "mimo-v2.6-flash")
                self.assertFalse(request.get("stream", False))
                self.assertEqual(request["response_format"]["type"], "json_schema")
                self.assertEqual(request["messages"][-1], {"role": "user", "content": message})
        self.assertEqual(len(self.requests), 3)

    async def test_schema_rejection_falls_back_to_json_object(self):
        self.responses.extend(
            [
                api_error(400, "response_format.type only accepts text and json_object"),
                completion('{"sentiment":"negative","emotion":"anxiety"}'),
            ]
        )
        result = await detect_emotion("我有点担心。")
        self.assertEqual(result.emotion, "anxiety")
        self.assertEqual(
            [request["response_format"]["type"] for request in self.requests],
            ["json_schema", "json_object"],
        )

    async def test_invalid_native_output_is_an_output_error(self):
        self.responses.append(completion('{"sentiment":"positive","emotion":"anxiety"}'))
        with self.assertRaises(EmotionOutputError):
            await detect_emotion("我有点担心。")
        self.assertEqual(len(self.requests), 1)

    async def test_empty_or_refused_native_output_is_an_output_error(self):
        for refusal in [None, "No classification available"]:
            with self.subTest(refusal=refusal):
                request_count = len(self.requests)
                self.responses.append(completion(None, refusal=refusal))
                with self.assertRaises(EmotionOutputError):
                    await detect_emotion("你好。")
                self.assertEqual(len(self.requests), request_count + 1)

    async def test_invalid_fallback_json_is_an_output_error(self):
        self.responses.extend(
            [api_error(422, "json_schema is not supported"), completion("this is not JSON")]
        )
        with self.assertRaises(EmotionOutputError):
            await detect_emotion("今天很开心。")
        self.assertEqual(len(self.requests), 2)

    async def test_unrelated_api_errors_do_not_trigger_fallback(self):
        for status, message in [(401, "Invalid API key"), (400, "Unknown model")]:
            with self.subTest(status=status):
                request_count = len(self.requests)
                self.responses.append(api_error(status, message))
                with self.assertRaises(EmotionAPIError):
                    await detect_emotion("你好。")
                self.assertEqual(len(self.requests), request_count + 1)

    async def test_missing_key_fails_before_calling_mimo(self):
        with patch.dict(os.environ, {"MIMO_API_KEY": ""}):
            with self.assertRaises(EmotionConfigurationError):
                await detect_emotion("你好。")
        self.assertEqual(self.requests, [])

    async def test_blank_input_fails_before_calling_mimo(self):
        with self.assertRaises(ValueError):
            await detect_emotion(" \n\t")
        self.assertEqual(self.requests, [])
