"""将聊天编排接入持久化的会话历史。"""

import asyncio
from weakref import WeakValueDictionary

from backend.database.storage import ChatStorage
from backend.models.schemas import ChatRequest, ChatResponse, HistoryMessage
from backend.services.chat_service import chat_service


class SessionService:
    def __init__(self, storage: ChatStorage) -> None:
        self._storage = storage
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def load_history(self, session_id: str) -> list[HistoryMessage]:
        """读取指定会话已提交的消息。"""
        return await asyncio.to_thread(self._storage.load_history, session_id)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """读取会话历史、生成回复，并在返回前保存本轮消息。"""
        session_id = request.session_id
        if session_id is None:
            return await self._run_turn(request)

        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        async with lock:
            return await self._run_turn(request)

    async def _run_turn(self, request: ChatRequest) -> ChatResponse:
        history: list[HistoryMessage] = []
        if request.session_id is not None:
            history = await self.load_history(request.session_id)

        response = await chat_service(request, history)
        await asyncio.to_thread(
            self._storage.save_turn, response.session_id, request.message, response.reply
        )
        return response
