"""服务层：串联进程1、路由、进程2，并维护多轮会话。

与 LangGraph 版的对应关系：

- 状态图节点 -> ``Agent``（emotion_analyzer + 三个回复 Agent）
- 条件边     -> ``route_after_emotion`` 纯函数
- MemorySaver -> ``ConversationStore``（只存干净的用户/助手文本）
- 节点 trace -> 本轮 ``trace`` 列表
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from .agents import AgentRegistry
from .config import Settings, get_settings
from .memory import ConversationStore
from .models import (
    ChatResponse,
    EmotionResult,
    HistoryMessage,
    HistoryResponse,
    ReplyContent,
    Strategy,
    SupportReply,
)
from .text_utils import compact_text

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 200
HISTORY_MESSAGE_CHARS = 500


def route_after_emotion(emotion: EmotionResult) -> Strategy:
    """纯函数路由：负面或危机走排解，正向走鼓励，其余走中性回应。"""

    if emotion.risk or emotion.label == "negative":
        return "soothe"
    if emotion.label == "positive":
        return "encourage"
    return "neutral_reply"


class ChatService:
    """一次 ``chat`` 就是一次完整的「进程1 -> 进程2」流水线。"""

    def __init__(
        self,
        *,
        registry: AgentRegistry | None = None,
        store: ConversationStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or AgentRegistry(self.settings)
        self.store = store or ConversationStore()

    async def chat(
        self, message: str, thread_id: str | None = None
    ) -> ChatResponse:
        message = message.strip()
        if not message:
            raise ValueError("消息不能为空")

        thread_id = (thread_id or "").strip() or uuid.uuid4().hex
        history = [
            {
                "role": item["role"],
                "content": compact_text(
                    item["content"], HISTORY_MESSAGE_CHARS
                ),
            }
            for item in self.store.get_items(
                thread_id, self.settings.max_history_messages
            )
        ]
        input_items: list[dict[str, Any]] = [
            *history,
            {
                "role": "user",
                "content": compact_text(
                    message, self.settings.max_message_chars
                ),
            },
        ]

        started = time.perf_counter()
        logger.info(
            "chat started thread=%s chars=%d history=%d model=%s",
            thread_id,
            len(message),
            len(history),
            self.settings.model,
        )

        # 进程1：情感识别
        emotion, emotion_degraded = await self.registry.analyze_emotion(
            input_items
        )

        # 条件路由：正向鼓励、负面排解、中性回应
        strategy = route_after_emotion(emotion)

        # 进程2：生成回复
        content, reply_degraded = await self.registry.generate_reply(
            strategy, emotion, input_items
        )

        suggestions = self._normalize_suggestions(
            strategy, content.suggestions, emotion
        )
        reply_text = content.reply_text.strip() or self._fallback_reply_text(
            strategy, emotion
        )
        reply = SupportReply(
            strategy=strategy,
            reply_text=reply_text,
            suggestions=suggestions,
        )

        trace = [
            f"analyze_emotion(label={emotion.label}, "
            f"emotion={emotion.emotion}, risk={emotion.risk}, "
            f"degraded={emotion_degraded})",
            f"{strategy}(suggestions={len(suggestions)}, "
            f"degraded={reply_degraded})",
            "finalize",
        ]
        response = ChatResponse(
            thread_id=thread_id,
            message=message,
            emotion=emotion,
            reply=reply,
            trace=trace,
        )

        # 只把干净的用户原话和最终回复写入历史。
        self.store.append_turn(thread_id, message, reply_text)

        logger.info(
            "chat finished thread=%s elapsed=%.2fs label=%s "
            "emotion_degraded=%s strategy=%s reply_degraded=%s",
            thread_id,
            time.perf_counter() - started,
            emotion.label,
            emotion_degraded,
            strategy,
            reply_degraded,
        )
        return response

    async def history(self, thread_id: str) -> HistoryResponse:
        items = self.store.get_items(thread_id, HISTORY_LIMIT)
        return HistoryResponse(
            thread_id=thread_id,
            messages=[
                HistoryMessage(role=item["role"], content=item["content"])
                for item in items
            ],
        )

    async def delete_thread(self, thread_id: str) -> None:
        self.store.clear(thread_id)
        logger.info("chat thread deleted thread=%s", thread_id)

    # --------------------------------------------------------------- 回复整理
    def _normalize_suggestions(
        self,
        strategy: Strategy,
        suggestions: list[str],
        emotion: EmotionResult,
    ) -> list[str]:
        cleaned: list[str] = []
        for item in suggestions:
            text = " ".join(str(item).split()).strip()
            if text and text not in cleaned:
                cleaned.append(text[:80])

        if strategy == "encourage":
            return cleaned[:2]
        if strategy == "soothe":
            defaults = (
                self._crisis_suggestions()
                if emotion.risk
                else self._soothe_suggestions()
            )
            for item in defaults:
                if len(cleaned) >= 2:
                    break
                if item not in cleaned:
                    cleaned.append(item)
            return cleaned[:3]
        return cleaned[:2]

    def _fallback_reply_text(
        self, strategy: Strategy, emotion: EmotionResult
    ) -> str:
        if strategy == "encourage":
            return (
                "能感受到你此刻的开心，这份顺利是你和身边的人一起努力换来的。"
                "把这份好心情记下来，之后想起也会觉得温暖。"
            )
        if strategy == "soothe":
            if emotion.risk:
                return (
                    "我很在意你现在的感受，也很高兴你愿意说出来。"
                    "请先不要一个人扛着，马上联系身边信任的人陪着你；"
                    "如果情况紧急，请拨打 120 或 110，"
                    "也可以拨打全国心理援助热线 12356。"
                )
            return (
                "听起来你现在真的很难受，先允许自己停下来喘口气。"
                "你不需要马上把所有事情都解决，能撑到现在已经很不容易了。"
            )
        return (
            "谢谢你愿意和我说这些。如果你愿意，可以再多说一点现在的感受，"
            "我会认真听。"
        )

    @staticmethod
    def _soothe_suggestions() -> list[str]:
        return [
            "先离开屏幕，喝点水、做几次深呼吸",
            "把此刻最压在心上的事写下来，只写一件",
            "找一个信任的人说说现在的感受",
        ]

    @staticmethod
    def _crisis_suggestions() -> list[str]:
        return [
            "马上联系一位信任的人，让对方陪着你",
            "情况紧急时拨打 120 / 110",
            "拨打全国心理援助热线 12356",
        ]
