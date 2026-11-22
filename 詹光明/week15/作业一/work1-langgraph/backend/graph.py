"""LangGraph 编排：进程1（情感识别）+ 进程2（鼓励 / 排解 / 中性回应）。

图结构：

    START -> analyze_emotion --+-> encourage      -> finalize -> END
                               +-> soothe         -> finalize -> END
                               +-> neutral_reply  -> finalize -> END

- ``analyze_emotion`` 只做情感识别，返回结构化的 ``EmotionResult``；
- ``route_after_emotion`` 是纯函数，按情感结果决定走哪个回复分支；
- 三个回复节点共享 ``_reply`` 逻辑，但使用不同的系统提示词；
- ``finalize`` 组装对外的 ``ChatResponse``，并把助手回复写入消息历史。

节点只返回自己修改的字段；是否继续由路由函数决定，便于单独测试每条边。
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from .config import Settings, get_settings
from .llm import structured_llm
from .models import ChatResponse, EmotionResult, ReplyContent, SupportReply
from .prompts import (
    CRISIS_REPLY_SYSTEM_PROMPT,
    EMOTION_SYSTEM_PROMPT,
    STRATEGY_SYSTEM_PROMPTS,
    build_emotion_user_prompt,
    build_reply_user_prompt,
)
from .text_utils import compact_text, last_user_text, render_history

logger = logging.getLogger(__name__)

Route = Literal["encourage", "soothe", "neutral_reply"]
ModelT = type[BaseModel]


class ChatState(TypedDict, total=False):
    """图状态。

    ``messages`` 用 ``add_messages`` 归约器累积多轮对话；
    ``emotion`` / ``reply`` / ``trace`` 每个请求都会被新值覆盖，
    因此 ``trace`` 只记录「本轮」的执行轨迹。
    """

    thread_id: str
    messages: Annotated[list[AnyMessage], add_messages]
    emotion: EmotionResult
    reply: SupportReply
    response: ChatResponse
    trace: list[str]


def _as_model(value: object, model_type: ModelT, source: str) -> BaseModel:
    """兼容 with_structured_output 直接返回 Pydantic 模型或 dict 的情况。"""

    if isinstance(value, model_type):
        return value
    if isinstance(value, dict):
        return model_type.model_validate(value)
    raise TypeError(
        f"{source} 返回了非 {model_type.__name__} 结果：{type(value).__name__}"
    )


def _preview(text: str, limit: int = 300) -> str:
    return compact_text(text, limit)


class EmotionSupportGraph:
    """图构建器：持有配置，节点方法即图节点。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ 进程1
    async def analyze_emotion(self, state: ChatState) -> dict:
        """调用一次结构化 LLM，识别本轮用户消息的情感。"""

        messages = list(state.get("messages", []))
        message = last_user_text(messages)
        if not message:
            raise ValueError("状态里没有可分析的用户消息")

        # 当前用户消息是 messages 的最后一条，历史里不应重复包含它。
        history_messages = (
            messages[:-1]
            if messages and isinstance(messages[-1], HumanMessage)
            else messages
        )
        history = render_history(
            history_messages, self.settings.max_history_messages
        )
        prompt = build_emotion_user_prompt(
            history, compact_text(message, self.settings.max_message_chars)
        )

        started = time.perf_counter()
        emotion, degraded = await self._recognize_emotion(prompt, message)
        elapsed = time.perf_counter() - started

        logger.info(
            "stage=emotion label=%s emotion=%s confidence=%.2f intensity=%d "
            "risk=%s degraded=%s elapsed=%.2fs reason=%s",
            emotion.label,
            emotion.emotion,
            emotion.confidence,
            emotion.intensity,
            emotion.risk,
            degraded,
            elapsed,
            _preview(emotion.reason),
        )
        return {
            "emotion": emotion,
            "trace": [
                f"analyze_emotion(label={emotion.label}, "
                f"emotion={emotion.emotion}, risk={emotion.risk}, "
                f"degraded={degraded})"
            ],
        }

    async def _recognize_emotion(
        self, prompt: str, message: str
    ) -> tuple[EmotionResult, bool]:
        """结构化识别 + 一次重试 + 规则兜底；保证请求不会因为模型抖动而失败。"""

        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                llm = structured_llm(
                    EmotionResult, self.settings.emotion_temperature
                )
                result = await llm.ainvoke(
                    [
                        SystemMessage(content=EMOTION_SYSTEM_PROMPT),
                        HumanMessage(content=prompt),
                    ]
                )
                emotion = _as_model(result, EmotionResult, "情感识别")
                assert isinstance(emotion, EmotionResult)

                # 模型漏判时，用关键词规则补一道保险。
                if self._has_crisis_signal(message) and not emotion.risk:
                    emotion = emotion.model_copy(update={"risk": True})
                return emotion, False
            except Exception as exc:  # noqa: BLE001 - 需要降级而不是中断
                last_error = exc
                logger.warning(
                    "stage=emotion_failed attempt=%d/2 error=%s", attempt, exc
                )

        crisis = self._has_crisis_signal(message)
        logger.error(
            "stage=emotion_degraded crisis=%s error=%s", crisis, last_error
        )
        return (
            EmotionResult(
                label="negative" if crisis else "neutral",
                emotion="distress" if crisis else "unknown",
                confidence=0.0,
                intensity=3,
                reason=(
                    "检测到危机相关表达，已切换到关怀模式。"
                    if crisis
                    else "情感识别模型暂时不可用，已按中性处理。"
                ),
                keywords=[
                    keyword
                    for keyword in self.settings.crisis_keywords
                    if keyword in message
                ][:5],
                risk=crisis,
            ),
            True,
        )

    # ------------------------------------------------------------------ 进程2
    async def encourage(self, state: ChatState) -> dict:
        return await self._reply(state, "encourage")

    async def soothe(self, state: ChatState) -> dict:
        return await self._reply(state, "soothe")

    async def neutral_reply(self, state: ChatState) -> dict:
        return await self._reply(state, "neutral_reply")

    async def _reply(self, state: ChatState, strategy: Route) -> dict:
        emotion = state.get("emotion") or self._fallback_emotion()
        messages = list(state.get("messages", []))
        message = last_user_text(messages)
        history_messages = (
            messages[:-1]
            if messages and isinstance(messages[-1], HumanMessage)
            else messages
        )
        history = render_history(
            history_messages, self.settings.max_history_messages
        )

        system_prompt = STRATEGY_SYSTEM_PROMPTS[strategy]
        if strategy == "soothe" and emotion.risk:
            system_prompt = CRISIS_REPLY_SYSTEM_PROMPT

        user_prompt = build_reply_user_prompt(
            emotion,
            history,
            compact_text(message, self.settings.max_message_chars),
            strategy,
        )

        started = time.perf_counter()
        content, degraded = await self._generate_reply(system_prompt, user_prompt)
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

        logger.info(
            "stage=reply strategy=%s risk=%s suggestions=%d degraded=%s "
            "elapsed=%.2fs reply=%s",
            strategy,
            emotion.risk,
            len(suggestions),
            degraded,
            time.perf_counter() - started,
            _preview(reply.reply_text),
        )
        return {
            "reply": reply,
            "trace": [
                *state.get("trace", []),
                f"{strategy}(suggestions={len(suggestions)}, degraded={degraded})",
            ],
        }

    async def _generate_reply(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[ReplyContent, bool]:
        """结构化生成回复 + 一次重试 + 固定兜底文案。"""

        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                llm = structured_llm(
                    ReplyContent, self.settings.reply_temperature
                )
                result = await llm.ainvoke(
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt),
                    ]
                )
                content = _as_model(result, ReplyContent, "回复生成")
                assert isinstance(content, ReplyContent)
                return content, False
            except Exception as exc:  # noqa: BLE001 - 需要降级而不是中断
                last_error = exc
                logger.warning(
                    "stage=reply_failed attempt=%d/2 error=%s", attempt, exc
                )

        logger.error("stage=reply_degraded error=%s", last_error)
        return ReplyContent(reply_text="", suggestions=[]), True

    # ------------------------------------------------------------------ 收尾
    def finalize(self, state: ChatState) -> dict:
        emotion = state.get("emotion") or self._fallback_emotion()
        reply = state.get("reply")
        if reply is None:
            reply = SupportReply(
                strategy="neutral_reply",
                reply_text=self._fallback_reply_text("neutral_reply", emotion),
                suggestions=[],
            )

        trace = [*state.get("trace", []), "finalize"]
        response = ChatResponse(
            thread_id=state.get("thread_id", ""),
            message=last_user_text(state.get("messages", [])),
            emotion=emotion,
            reply=reply,
            trace=trace,
        )
        logger.info(
            "stage=final thread=%s strategy=%s elapsed_trace=%s",
            response.thread_id,
            response.reply.strategy,
            " -> ".join(response.trace),
        )
        return {
            "response": response,
            "messages": [AIMessage(content=reply.reply_text)],
            "trace": ["finalize"],
        }

    # ------------------------------------------------------------------ 路由
    def route_after_emotion(self, state: ChatState) -> Route:
        """纯函数：负面或危机走排解，正向走鼓励，其余走中性回应。"""

        emotion = state.get("emotion")
        if emotion is None:
            return "neutral_reply"
        if emotion.risk or emotion.label == "negative":
            return "soothe"
        if emotion.label == "positive":
            return "encourage"
        return "neutral_reply"

    # ------------------------------------------------------------------ 构图
    def build(self, *, checkpointer=None) -> CompiledStateGraph:
        builder = StateGraph(ChatState)
        builder.add_node("analyze_emotion", self.analyze_emotion)
        builder.add_node("encourage", self.encourage)
        builder.add_node("soothe", self.soothe)
        builder.add_node("neutral_reply", self.neutral_reply)
        builder.add_node("finalize", self.finalize)

        builder.add_edge(START, "analyze_emotion")
        builder.add_conditional_edges(
            "analyze_emotion",
            self.route_after_emotion,
            {
                "encourage": "encourage",
                "soothe": "soothe",
                "neutral_reply": "neutral_reply",
            },
        )
        builder.add_edge("encourage", "finalize")
        builder.add_edge("soothe", "finalize")
        builder.add_edge("neutral_reply", "finalize")
        builder.add_edge("finalize", END)

        return builder.compile(checkpointer=checkpointer)

    # --------------------------------------------------------------- 辅助方法
    def _has_crisis_signal(self, text: str) -> bool:
        lowered = text.lower()
        return any(
            keyword.lower() in lowered
            for keyword in self.settings.crisis_keywords
        )

    def _fallback_emotion(self) -> EmotionResult:
        return EmotionResult(
            label="neutral",
            emotion="unknown",
            confidence=0.0,
            intensity=3,
            reason="情感识别结果缺失，已按中性处理。",
            keywords=[],
            risk=False,
        )

    def _fallback_reply_text(
        self, strategy: Route, emotion: EmotionResult
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

    def _normalize_suggestions(
        self,
        strategy: Route,
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
