"""后端编排（OpenAI Agents 版）：conductor + handoffs。

    conductor（分诊）
        ├─ tool analyze_sentiment ──▶ 进程1（情感识别）
        └─ handoff ──▶ encourager / comforter / neutral_responder
                            └─ tool generate_reply ──▶ 进程2（对应风格）

与"全部用工具"的写法相比，这里回复不是由 conductor 直接调用工具完成，
而是先把对话交接给专家 Agent，由专家调用进程2 生成最终回复。
无论走哪条路径，最终回复都必须来自进程2；后端会统计工具调用，
模型绕过进程1/进程2 时自动回退到直接串联。
"""
from __future__ import annotations

import json
from typing import Any, Callable

from agents import Agent, Runner, function_tool, handoff

from shared.config import get_settings
from shared.protocol import (
    ReplyResult,
    SentimentRequest,
    SentimentResult,
    SupportRequest,
    new_rid,
)
from shared.runtime import call_stage, log

from .runtime_setup import configure_agents

_STYLE_BY_SENTIMENT = {"positive": "encourage", "negative": "comfort"}
_SENTIMENT_BY_STYLE = {
    "encourage": "positive",
    "comfort": "negative",
    "neutral": "neutral",
}
_STYLE_LABEL = {"encourage": "鼓励型", "comfort": "排解型", "neutral": "中性回应"}

_CONDUCTOR_INSTRUCTIONS = """你是情绪陪伴流水线的分诊调度器。
严格按顺序执行：
1. 调用 analyze_sentiment 工具，把用户原话传进去，得到 JSON 情感识别结果；
2. 根据其中的 sentiment 字段交接对话：
   - positive → 调用 transfer_to_encourager
   - negative → 调用 transfer_to_comforter
   - neutral  → 调用 transfer_to_neutral_responder
3. 交接后由专家 Agent 完成回复；你不要自己编写任何安慰或鼓励内容，也不要输出最终回复。
"""


def _specialist_instructions(style: str) -> str:
    return f"""你是{_STYLE_LABEL[style]}陪伴者。
本轮对话已经过情感分诊，你需要调用 generate_reply 工具完成回复：
- 传入用户原话，以及前面对话中情感识别工具返回的 reason（如无则留空）；
- generate_reply 会把请求发给进程2，并返回最终回复；
- 把工具返回的文本原样作为你的最终回答，不要自己编写、改写或补充内容。
"""


def _style_by_sentiment(sentiment: str) -> str:
    return _STYLE_BY_SENTIMENT.get(sentiment, "neutral")


def build_orchestrator(
    sentiment_in: Any,
    sentiment_out: Any,
    support_in: Any,
    support_out: Any,
) -> Callable[[str, str], str]:
    """后端进程启动时调用：返回 orchestrator(text, session_id) -> reply。"""

    def analyze(text: str) -> SentimentResult:
        request = SentimentRequest(rid=new_rid(), text=text)
        return call_stage(sentiment_in, sentiment_out, request, SentimentResult)

    def support(
        text: str,
        sentiment: str,
        style: str,
        reason: str,
        history: list[dict[str, str]] | None = None,
    ) -> ReplyResult:
        request = SupportRequest(
            rid=new_rid(),
            text=text,
            sentiment=sentiment,  # type: ignore[arg-type]
            reason=reason,
            style=style,  # type: ignore[arg-type]
            history=list(history or []),
        )
        return call_stage(support_in, support_out, request, ReplyResult)

    # 本轮 handoff / 工具调用统计，用于保证最终回复确实来自进程2
    stat: dict[str, Any] = {"analyze_calls": 0, "reply_calls": 0, "last_reply": ""}

    @function_tool
    def analyze_sentiment(text: str) -> str:
        """调用进程1 做情感识别。返回 JSON 字符串（sentiment / confidence / reason）。"""
        result = analyze(text)
        stat["analyze_calls"] += 1
        log(
            f"进程1 情感识别：{result.sentiment} "
            f"(confidence={result.confidence:.2f}, source={result.source})"
        )
        return json.dumps(
            {
                "sentiment": result.sentiment,
                "confidence": result.confidence,
                "reason": result.reason,
            },
            ensure_ascii=False,
        )

    def make_reply_tool(style: str):
        fixed_sentiment = _SENTIMENT_BY_STYLE[style]

        @function_tool
        def generate_reply(user_text: str, reason: str = "") -> str:
            """把用户原话发给进程2，生成最终回复。reason 是情感识别理由，可为空。"""
            result = support(user_text, fixed_sentiment, style, reason)
            stat["reply_calls"] += 1
            stat["last_reply"] = result.reply
            log(f"进程2 回复风格：{style} (source={result.source})")
            return result.reply

        return generate_reply

    configure_agents()
    model = get_settings().model

    encourage_agent = Agent(
        name="encourager",
        instructions=_specialist_instructions("encourage"),
        tools=[make_reply_tool("encourage")],
        model=model,
    )
    comfort_agent = Agent(
        name="comforter",
        instructions=_specialist_instructions("comfort"),
        tools=[make_reply_tool("comfort")],
        model=model,
    )
    neutral_agent = Agent(
        name="neutral_responder",
        instructions=_specialist_instructions("neutral"),
        tools=[make_reply_tool("neutral")],
        model=model,
    )

    conductor = Agent(
        name="conductor",
        instructions=_CONDUCTOR_INSTRUCTIONS,
        tools=[analyze_sentiment],
        handoffs=[
            handoff(
                encourage_agent,
                tool_name_override="transfer_to_encourager",
                tool_description_override="用户情感为 positive 时，把对话交给鼓励型陪伴者。",
            ),
            handoff(
                comfort_agent,
                tool_name_override="transfer_to_comforter",
                tool_description_override="用户情感为 negative 时，把对话交给排解型陪伴者。",
            ),
            handoff(
                neutral_agent,
                tool_name_override="transfer_to_neutral_responder",
                tool_description_override="用户情感为 neutral 时，把对话交给中性回应者。",
            ),
        ],
        model=model,
    )

    def run_direct(text: str, session_id: str) -> str:
        """兜底路径：不经 LLM，后端直接按顺序串联两个进程。"""
        sentiment_result = analyze(text)
        log(
            f"[session={session_id}] 进程1 情感识别：{sentiment_result.sentiment} "
            f"(confidence={sentiment_result.confidence:.2f}, "
            f"source={sentiment_result.source})"
        )
        style = _style_by_sentiment(sentiment_result.sentiment)
        reply = support(
            sentiment_result.text,
            sentiment_result.sentiment,
            style,
            sentiment_result.reason,
        )
        log(f"[session={session_id}] 进程2 回复风格：{reply.style} (source={reply.source})")
        return reply.reply

    def run(text: str, session_id: str = "default") -> str:
        settings = get_settings()
        if not settings.llm_enabled:
            return run_direct(text, session_id)

        stat["analyze_calls"] = 0
        stat["reply_calls"] = 0
        stat["last_reply"] = ""

        try:
            # 结果文本不直接采用：最终回复一律以进程2 写入 stat["last_reply"] 的为准
            Runner.run_sync(conductor, text, max_turns=10)
        except Exception as exc:
            log(f"[session={session_id}] handoff 流程失败，回退直接串联：{exc}")
            return run_direct(text, session_id)

        if (
            stat["analyze_calls"] < 1
            or stat["reply_calls"] < 1
            or not stat["last_reply"]
        ):
            log(f"[session={session_id}] handoff 流程没有完整经过进程1/进程2，回退直接串联")
            return run_direct(text, session_id)

        return str(stat["last_reply"])

    return run
