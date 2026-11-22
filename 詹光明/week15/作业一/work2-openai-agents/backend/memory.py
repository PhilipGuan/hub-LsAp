"""内存会话存储。

只保存「用户原话」和「最终回复正文」，不保存中间的情感 JSON / 回复 JSON，
这样下一轮 Agent 看到的历史是干净的对话，而不是结构化信封。

OpenAI Agents SDK 自带的 Session 也可以做持久化，但 Runner 会自动把 Agent
的最终输出原样写回会话；本项目为了保持历史干净，在服务层显式管理。
"""

from __future__ import annotations

from typing import Any


class ConversationStore:
    """thread_id -> 消息列表，进程内有效，重启后端后清空。"""

    def __init__(self) -> None:
        self._threads: dict[str, list[dict[str, Any]]] = {}

    def get_items(
        self, thread_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        items = self._threads.get(thread_id, [])
        if limit is not None and limit > 0:
            items = items[-limit:]
        return [dict(item) for item in items]

    def append_turn(
        self, thread_id: str, user_message: str, assistant_message: str
    ) -> None:
        items = self._threads.setdefault(thread_id, [])
        items.append({"role": "user", "content": user_message})
        items.append({"role": "assistant", "content": assistant_message})

    def clear(self, thread_id: str) -> None:
        self._threads.pop(thread_id, None)

    def __len__(self) -> int:
        return len(self._threads)
