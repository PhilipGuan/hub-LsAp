import asyncio
import os
import sqlite3
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

import httpx2

from backend.agents.emotion_agent import EmotionAPIError
from backend.database.storage import ChatStorage
from backend.main import app
from backend.models.schemas import EmotionResult


class ChatEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "chat.db"
        environment = patch.dict(os.environ, {"DATABASE_PATH": str(self.database)})
        environment.start()
        self.addCleanup(environment.stop)

        self.lifespan = AsyncExitStack()
        self.addAsyncCleanup(self.lifespan.aclose)
        await self.lifespan.enter_async_context(app.router.lifespan_context(app))
        transport = httpx2.ASGITransport(app=app)
        self.client = httpx2.AsyncClient(transport=transport, base_url="http://testserver")
        self.addAsyncCleanup(self.client.aclose)

        emotion = EmotionResult(sentiment="negative", emotion="anxiety")
        detect_patch = patch(
            "backend.services.chat_service.detect_emotion", return_value=emotion
        )
        respond_patch = patch(
            "backend.services.chat_service.generate_response", return_value="我们可以一起想个办法。"
        )
        self.detect = detect_patch.start()
        self.respond = respond_patch.start()
        self.addCleanup(detect_patch.stop)
        self.addCleanup(respond_patch.stop)

    async def test_returns_full_reply_without_debug_by_default(self):
        response = await self.client.post("/chat", json={"message": "我有点担心。"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"session_id", "reply"})
        self.assertEqual(UUID(payload["session_id"]).version, 4)
        self.assertEqual(payload["reply"], "我们可以一起想个办法。")
        self.detect.assert_awaited_once_with("我有点担心。")

    async def test_returns_agent_status_and_emotion_when_debug_is_enabled(self):
        response = await self.client.post(
            "/chat", json={"message": "我有点担心。", "session_id": "session-1", "debug": True}
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], "session-1")
        self.assertEqual(
            payload["debug"],
            {
                "emotion_agent_status": "success",
                "response_agent_status": "success",
                "emotion": {"sentiment": "negative", "emotion": "anxiety"},
            },
        )

    async def test_rejects_blank_message_before_running_agents(self):
        response = await self.client.post("/chat", json={"message": " \t\n"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"][0]["loc"], ["body", "message"])
        self.detect.assert_not_awaited()
        self.respond.assert_not_awaited()

    async def test_second_turn_loads_first_turn_and_saves_both_messages(self):
        first = await self.client.post("/chat", json={"message": "我要准备答辩。"})
        session_id = first.json()["session_id"]
        second = await self.client.post(
            "/chat", json={"message": "第一步该做什么？", "session_id": session_id}
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["session_id"], session_id)
        history = [
            {"role": "user", "content": "我要准备答辩。"},
            {"role": "assistant", "content": first.json()["reply"]},
        ]
        self.assertEqual(self.respond.await_args.args[2], history)
        self.assertEqual(
            ChatStorage(self.database).load_history(session_id),
            [
                *history,
                {"role": "user", "content": "第一步该做什么？"},
                {"role": "assistant", "content": second.json()["reply"]},
            ],
        )

    async def test_restarted_app_restores_only_the_requested_session(self):
        first = await self.client.post("/chat", json={"message": "记住我在准备答辩。"})
        session_id = first.json()["session_id"]

        await self.lifespan.aclose()
        await self.lifespan.enter_async_context(app.router.lifespan_context(app))

        other = await self.client.post("/chat", json={"message": "这是另一个会话。"})
        self.assertNotEqual(other.json()["session_id"], session_id)
        self.assertFalse(self.respond.await_args.args[2])
        resumed = await self.client.post(
            "/chat", json={"message": "我刚在准备什么？", "session_id": session_id}
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(
            self.respond.await_args.args[2],
            [
                {"role": "user", "content": "记住我在准备答辩。"},
                {"role": "assistant", "content": first.json()["reply"]},
            ],
        )

    async def test_history_endpoint_returns_only_the_requested_session_in_order(self):
        session_id = "session/1 + test"
        await self.client.post("/chat", json={"message": "第一轮", "session_id": session_id})
        await self.client.post("/chat", json={"message": "另一个会话"})
        await self.client.post("/chat", json={"message": "第二轮", "session_id": session_id})

        response = await self.client.get("/history", params={"session_id": session_id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {"role": "user", "content": "第一轮"},
                {"role": "assistant", "content": self.respond.return_value},
                {"role": "user", "content": "第二轮"},
                {"role": "assistant", "content": self.respond.return_value},
            ],
        )
        unknown = await self.client.get("/history", params={"session_id": "unknown"})
        self.assertEqual(unknown.json(), [])
        blank = await self.client.get("/history", params={"session_id": " "})
        self.assertEqual(blank.status_code, 422)

    async def test_overlapping_requests_in_the_same_session_see_committed_history(self):
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def respond(message, emotion, history):
            if message == "第一轮":
                first_started.set()
                await release_first.wait()
            return f"回复{message}"

        self.respond.side_effect = respond
        first = asyncio.create_task(
            self.client.post("/chat", json={"message": "第一轮", "session_id": "shared"})
        )
        self.addCleanup(first.cancel)
        await asyncio.wait_for(first_started.wait(), timeout=2)
        second = asyncio.create_task(
            self.client.post("/chat", json={"message": "第二轮", "session_id": "shared"})
        )
        self.addCleanup(second.cancel)

        try:
            await asyncio.sleep(0.1)
            started_before_commit = self.respond.await_count
        finally:
            release_first.set()

        responses = await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
        self.assertEqual(started_before_commit, 1)
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(
            self.respond.await_args.args[2],
            [
                {"role": "user", "content": "第一轮"},
                {"role": "assistant", "content": "回复第一轮"},
            ],
        )

    async def test_returns_503_when_the_turn_cannot_be_saved(self):
        with (
            patch.object(ChatStorage, "save_turn", side_effect=sqlite3.OperationalError("database locked")),
            self.assertLogs("backend.main", level="WARNING"),
        ):
            response = await self.client.post(
                "/chat", json={"message": "你好。", "session_id": "session-1"}
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("稍后再试", response.json()["detail"])
        self.assertEqual(ChatStorage(self.database).load_history("session-1"), [])

    async def test_model_failure_returns_service_reply_and_debug_status(self):
        self.detect.side_effect = EmotionAPIError("provider unavailable")
        with self.assertLogs("backend.services.chat_service", level="WARNING"):
            response = await self.client.post("/chat", json={"message": "你好。", "debug": True})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("稍后再试", payload["reply"])
        self.assertEqual(
            payload["debug"],
            {"emotion_agent_status": "failed", "response_agent_status": "skipped"},
        )
        self.respond.assert_not_awaited()
