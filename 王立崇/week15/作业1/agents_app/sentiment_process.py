"""进程1：情感识别（OpenAI Agents 版）。

优先使用 SDK 的结构化输出（output_type），由 SDK 强制校验
sentiment / confidence / reason 三个字段。

部分 OpenAI 兼容服务（例如只支持 json_object 的模型）不支持 json_schema，
此时自动降级为"普通 Agent + JSON 文本解析"；
两者都失败时才退回本地词典规则。
"""
from __future__ import annotations

from typing import Literal

from agents import Agent, Runner
from pydantic import BaseModel, Field

from shared import sentiment_model
from shared.config import get_settings
from shared.llm import extract_json
from shared.protocol import SentimentRequest, SentimentResult
from shared.runtime import log
from shared.sentiment import rule_sentiment

from .runtime_setup import configure_agents


class SentimentOutput(BaseModel):
    """进程1 的结构化输出。"""

    sentiment: Literal["positive", "negative", "neutral"] = Field(
        description="整体情绪倾向"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="置信度，0 到 1")
    reason: str = Field(description="不超过 30 字的中文判定理由")


_INSTRUCTIONS = """你是中文情感识别专家。
判断用户这句话的整体情绪倾向：
- positive：开心、感激、成就、期待等正向情绪；
- negative：焦虑、难过、愤怒、疲惫、孤独等负向情绪；
- neutral：陈述事实，或情绪不明显。
只做判断，不要安慰用户，不要输出多余内容。"""

_JSON_INSTRUCTIONS = _INSTRUCTIONS + """

只输出一个 JSON 对象，不要输出任何其他内容：
{"sentiment": "positive" | "negative" | "neutral", "confidence": 0.0, "reason": "不超过 30 字"}"""

_agent: Agent | None = None
_json_agent: Agent | None = None


def _get_agent() -> Agent:
    global _agent
    if _agent is None:
        configure_agents()
        _agent = Agent(
            name="sentiment_analyst",
            instructions=_INSTRUCTIONS,
            output_type=SentimentOutput,
            model=get_settings().model,
        )
    return _agent


def _get_json_agent() -> Agent:
    global _json_agent
    if _json_agent is None:
        configure_agents()
        _json_agent = Agent(
            name="sentiment_analyst_text",
            instructions=_JSON_INSTRUCTIONS,
            model=get_settings().model,
        )
    return _json_agent


def _clamp(value: object, default: float = 0.8) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _run_structured(text: str) -> SentimentOutput | None:
    try:
        result = Runner.run_sync(_get_agent(), text, max_turns=3)
        output = result.final_output
        if isinstance(output, SentimentOutput):
            return output
        log("进程1 结构化 Agent 没有返回 SentimentOutput")
    except Exception as exc:
        log(f"进程1 结构化 Agent 失败（可能不支持 json_schema）：{exc}")
    return None


def _run_json_agent(text: str) -> SentimentOutput | None:
    try:
        result = Runner.run_sync(_get_json_agent(), text, max_turns=2)
        data = extract_json(str(result.final_output or ""))
        label = str(data.get("sentiment", "")).strip().lower()
        if label in {"positive", "negative", "neutral"}:
            return SentimentOutput(
                sentiment=label,  # type: ignore[arg-type]
                confidence=_clamp(data.get("confidence")),
                reason=str(data.get("reason", ""))[:80],
            )
        log(f"进程1 JSON Agent 返回了非法标签：{label!r}")
    except Exception as exc:
        log(f"进程1 JSON Agent 失败：{exc}")
    return None


def handle_sentiment(request: SentimentRequest) -> SentimentResult:
    settings = get_settings()

    # 1) 本地微调模型（SENTIMENT_MODEL_PATH 配置后优先使用）
    if sentiment_model.enabled():
        try:
            label, confidence, reason = sentiment_model.classify(request.text)
            log("进程1 本地模型完成情感识别")
            return SentimentResult(
                rid=request.rid,
                text=request.text,
                sentiment=label,  # type: ignore[arg-type]
                confidence=confidence,
                reason=reason,
                source="model",
            )
        except Exception as exc:
            log(f"进程1 本地模型不可用，继续尝试 Agent / 规则：{exc}")

    # 2) Agents SDK
    if settings.llm_enabled:
        output = _run_structured(request.text) or _run_json_agent(request.text)
        if output is not None:
            log("进程1 Agent 完成情感识别")
            return SentimentResult(
                rid=request.rid,
                text=request.text,
                sentiment=output.sentiment,
                confidence=float(output.confidence),
                reason=output.reason[:80],
                source="llm",
            )

    label, confidence, reason = rule_sentiment(request.text)
    return SentimentResult(
        rid=request.rid,
        text=request.text,
        sentiment=label,
        confidence=confidence,
        reason=reason,
        source="rule",
    )
