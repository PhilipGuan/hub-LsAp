"""OpenAI Agents SDK 的 Agent 定义与调用封装。

这里有四个 Agent：

- ``emotion_analyzer``：进程1，负责情感识别；
- ``encourager`` / ``soother`` / ``neutral_companion``：进程2 的三条回复分支。

路由由 ``service.py`` 里的纯函数决定，而不是交给 Agent 自由 handoff，
这样「正向鼓励、负面排解」的流程是代码控制、可测试的。

结构化输出说明
--------------
DeepSeek 等兼容端点不支持 ``response_format=json_schema``，所以这里不使用
``Agent(output_type=...)``，而是：

1. 提示词要求模型只输出一个 JSON 对象；
2. 请求时通过 ``extra_body`` 打开 ``json_object`` 模式（可配置关闭）；
3. 用 Pydantic 在本地校验，失败重试一次，再失败走兜底。

这样同一份代码既能跑 OpenAI，也能跑 DeepSeek / Qwen 等兼容端点。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agents import (
    Agent,
    Model,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
    RunContextWrapper,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from .config import Settings, get_settings
from .models import EmotionResult, ReplyContent, Strategy
from .prompts import EMOTION_SYSTEM_PROMPT, build_reply_system_prompt
from .text_utils import compact_text, last_user_text, parse_json_model

logger = logging.getLogger(__name__)

STRATEGIES: tuple[Strategy, ...] = ("encourage", "soothe", "neutral_reply")


class AgentRegistry:
    """持有模型客户端和四个 Agent；每次调用都是独立的 ``Runner.run``。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        model: Model | None = None,
    ) -> None:
        self.settings = settings or get_settings()

        # 使用非 OpenAI 端点时关闭 SDK 的 tracing，避免把请求发往 OpenAI。
        set_tracing_disabled(True)

        if model is None:
            client = AsyncOpenAI(
                api_key=self.settings.api_key or "not-configured",
                base_url=self.settings.base_url,
                timeout=self.settings.llm_timeout_seconds,
                max_retries=self.settings.llm_max_retries,
            )
            model = OpenAIChatCompletionsModel(
                model=self.settings.model,
                openai_client=client,
            )
        self.model = model

        self.emotion_agent = Agent(
            name="emotion_analyzer",
            instructions=EMOTION_SYSTEM_PROMPT,
            model=self.model,
            model_settings=self._model_settings(
                self.settings.emotion_temperature
            ),
        )
        self.reply_agents: dict[Strategy, Agent] = {
            strategy: self._build_reply_agent(strategy) for strategy in STRATEGIES
        }
        self.run_config = RunConfig(
            tracing_disabled=True,
            trace_include_sensitive_data=False,
            workflow_name="emotion-support",
        )

    # ------------------------------------------------------------------ Agent
    def _model_settings(self, temperature: float) -> ModelSettings:
        kwargs: dict[str, Any] = {"temperature": temperature}
        if self.settings.json_mode:
            # DeepSeek / OpenAI 都支持 json_object；schema 由提示词约束。
            kwargs["extra_body"] = {
                "response_format": {"type": "json_object"}
            }
        return ModelSettings(**kwargs)

    def _build_reply_agent(self, strategy: Strategy) -> Agent:
        def instructions(
            context: RunContextWrapper[EmotionResult], agent: Agent
        ) -> str:
            emotion = context.context or self._fallback_emotion("")
            return build_reply_system_prompt(strategy, emotion)

        return Agent(
            name=f"{strategy}_agent",
            instructions=instructions,
            model=self.model,
            model_settings=self._model_settings(
                self.settings.reply_temperature
            ),
        )

    # ------------------------------------------------------------------ 进程1
    async def analyze_emotion(
        self, input_items: list[dict[str, Any]]
    ) -> tuple[EmotionResult, bool]:
        """返回 (情感结果, 是否降级)。"""

        message = last_user_text(input_items)
        last_error: Exception | None = None

        for attempt in range(1, 3):
            started = time.perf_counter()
            try:
                result = await Runner.run(
                    self.emotion_agent,
                    input=input_items,
                    max_turns=self.settings.agent_max_turns,
                    run_config=self.run_config,
                )
                emotion = parse_json_model(
                    self._output_text(result), EmotionResult
                )
                assert isinstance(emotion, EmotionResult)

                # 模型漏判时，用关键词规则补一道保险。
                if self._has_crisis_signal(message) and not emotion.risk:
                    emotion = emotion.model_copy(update={"risk": True})

                logger.info(
                    "stage=emotion attempt=%d label=%s emotion=%s "
                    "confidence=%.2f intensity=%d risk=%s elapsed=%.2fs "
                    "reason=%s",
                    attempt,
                    emotion.label,
                    emotion.emotion,
                    emotion.confidence,
                    emotion.intensity,
                    emotion.risk,
                    time.perf_counter() - started,
                    compact_text(emotion.reason, 300),
                )
                return emotion, False
            except Exception as exc:  # noqa: BLE001 - 需要降级而不是中断
                last_error = exc
                logger.warning(
                    "stage=emotion_failed attempt=%d/2 error=%s", attempt, exc
                )

        logger.error("stage=emotion_degraded error=%s", last_error)
        return self._fallback_emotion(message), True

    # ------------------------------------------------------------------ 进程2
    async def generate_reply(
        self,
        strategy: Strategy,
        emotion: EmotionResult,
        input_items: list[dict[str, Any]],
    ) -> tuple[ReplyContent, bool]:
        """返回 (结构化回复, 是否降级)。"""

        agent = self.reply_agents[strategy]
        last_error: Exception | None = None

        for attempt in range(1, 3):
            started = time.perf_counter()
            try:
                result = await Runner.run(
                    agent,
                    input=input_items,
                    context=emotion,
                    max_turns=self.settings.agent_max_turns,
                    run_config=self.run_config,
                )
                content = parse_json_model(
                    self._output_text(result), ReplyContent
                )
                assert isinstance(content, ReplyContent)
                logger.info(
                    "stage=reply strategy=%s attempt=%d risk=%s "
                    "suggestions=%d elapsed=%.2fs reply=%s",
                    strategy,
                    attempt,
                    emotion.risk,
                    len(content.suggestions),
                    time.perf_counter() - started,
                    compact_text(content.reply_text, 300),
                )
                return content, False
            except Exception as exc:  # noqa: BLE001 - 需要降级而不是中断
                last_error = exc
                logger.warning(
                    "stage=reply_failed strategy=%s attempt=%d/2 error=%s",
                    strategy,
                    attempt,
                    exc,
                )

        logger.error(
            "stage=reply_degraded strategy=%s error=%s", strategy, last_error
        )
        return ReplyContent(reply_text="", suggestions=[]), True

    # --------------------------------------------------------------- 辅助方法
    @staticmethod
    def _output_text(result: Any) -> str:
        output = getattr(result, "final_output", None)
        if isinstance(output, str):
            text = output.strip()
        elif output is None:
            text = ""
        else:
            text = str(output).strip()
        if not text:
            raise ValueError("Agent 没有返回文本内容")
        return text

    def _has_crisis_signal(self, text: str) -> bool:
        lowered = text.lower()
        return any(
            keyword.lower() in lowered
            for keyword in self.settings.crisis_keywords
        )

    def _fallback_emotion(self, message: str) -> EmotionResult:
        crisis = self._has_crisis_signal(message)
        return EmotionResult(
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
        )
