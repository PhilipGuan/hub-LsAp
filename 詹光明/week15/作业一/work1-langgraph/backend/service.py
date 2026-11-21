"""对外的服务层：编译状态图、管理多轮会话、封装调用与异常。

流程本身全部在 ``graph.py`` 的状态图里；这里只负责：
生成 thread_id、用 MemorySaver 保存会话、读取历史、删除会话。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph.state import CompiledStateGraph

from .config import Settings, get_settings
from .graph import EmotionSupportGraph
from .models import ChatResponse, HistoryMessage, HistoryResponse
from .text_utils import message_text

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 25
HISTORY_LIMIT = 200

# 图状态里保存的是自定义 Pydantic 模型；新版 LangGraph 的 msgpack 反序列化
# 需要显式放行这些类型，否则会打印 "unregistered type" 告警。
_CHECKPOINT_TYPES = [
    ("backend.models", "EmotionResult"),
    ("backend.models", "SupportReply"),
    ("backend.models", "ChatResponse"),
]


def _default_checkpointer() -> MemorySaver:
    serde = JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_TYPES)
    return MemorySaver(serde=serde)


class ChatService:
    """编译一次图，之后每个 ``chat`` 都是一次独立的图执行。"""

    def __init__(
        self,
        *,
        graph: CompiledStateGraph | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.checkpointer = checkpointer or _default_checkpointer()
        self.graph = graph or EmotionSupportGraph(self.settings).build(
            checkpointer=self.checkpointer
        )

    async def chat(
        self, message: str, thread_id: str | None = None
    ) -> ChatResponse:
        message = message.strip()
        if not message:
            raise ValueError("消息不能为空")

        thread_id = (thread_id or "").strip() or uuid.uuid4().hex
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": RECURSION_LIMIT,
        }

        started = time.perf_counter()
        logger.info(
            "chat started thread=%s chars=%d model=%s",
            thread_id,
            len(message),
            self.settings.model,
        )
        final_state = await self.graph.ainvoke(
            {
                "thread_id": thread_id,
                "messages": [HumanMessage(content=message)],
                "trace": [],
            },
            config=config,
        )
        response = final_state.get("response")
        if not isinstance(response, ChatResponse):
            raise RuntimeError("状态图未生成 ChatResponse")

        logger.info(
            "chat finished thread=%s elapsed=%.2fs label=%s strategy=%s",
            thread_id,
            time.perf_counter() - started,
            response.emotion.label,
            response.reply.strategy,
        )
        return response

    async def history(self, thread_id: str) -> HistoryResponse:
        snapshot = await self.graph.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        values = snapshot.values or {}
        messages: list[HistoryMessage] = []
        for message in values.get("messages", [])[-HISTORY_LIMIT:]:
            if isinstance(message, HumanMessage):
                messages.append(
                    HistoryMessage(role="user", content=message_text(message))
                )
            elif isinstance(message, AIMessage):
                messages.append(
                    HistoryMessage(
                        role="assistant", content=message_text(message)
                    )
                )
        return HistoryResponse(thread_id=thread_id, messages=messages)

    async def delete_thread(self, thread_id: str) -> None:
        try:
            await self.checkpointer.adelete_thread(thread_id)
        except (AttributeError, NotImplementedError):  # pragma: no cover
            logger.warning("checkpointer 不支持删除会话 thread=%s", thread_id)
            return
        logger.info("chat thread deleted thread=%s", thread_id)
