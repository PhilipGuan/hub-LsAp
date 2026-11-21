"""进程1 / 进程2 的具体实现（LangGraph 版本）。

这两个函数分别运行在独立的 OS 进程里，只通过队列收发消息：
* handle_sentiment —— 进程1：情感识别
* handle_support   —— 进程2：按情感倾向生成鼓励 / 排解回复
"""
from __future__ import annotations

from shared import sentiment_model
from shared.config import get_settings
from shared.history import format_history
from shared.llm import complete_json, complete_text
from shared.protocol import (
    ReplyResult,
    SentimentRequest,
    SentimentResult,
    SupportRequest,
)
from shared.runtime import log
from shared.sentiment import fallback_reply, rule_sentiment

# --------------------------------------------------------------------------- #
# 进程1：情感识别
# --------------------------------------------------------------------------- #
_SENTIMENT_SYSTEM = """你是中文情感识别引擎。
阅读用户输入，判断整体情绪倾向，只输出一个 JSON 对象：
{"sentiment": "positive" | "negative" | "neutral", "confidence": 0 到 1 之间的小数, "reason": "不超过 30 字的中文理由"}

判定标准：
- positive：开心、感激、成就、期待等正向情绪；
- negative：焦虑、难过、愤怒、疲惫、孤独等负向情绪；
- neutral：陈述事实，或情绪不明显。
不要输出 JSON 之外的任何内容。"""


def handle_sentiment(request: SentimentRequest) -> SentimentResult:
    settings = get_settings()

    # 1) 本地微调模型（SENTIMENT_MODEL_PATH 配置后优先使用）
    if sentiment_model.enabled():
        try:
            label, confidence, reason = sentiment_model.classify(request.text)
            return SentimentResult(
                rid=request.rid,
                text=request.text,
                sentiment=label,  # type: ignore[arg-type]
                confidence=confidence,
                reason=reason,
                source="model",
            )
        except Exception as exc:
            log(f"进程1 本地模型不可用，继续尝试 LLM / 规则：{exc}")

    # 2) LLM
    if settings.llm_enabled:
        try:
            data = complete_json(
                _SENTIMENT_SYSTEM,
                f"用户输入：{request.text}",
            )
            label = str(data.get("sentiment", "")).strip().lower()
            if label in {"positive", "negative", "neutral"}:
                raw_confidence = data.get("confidence", 0.8)
                try:
                    confidence = max(0.0, min(1.0, float(raw_confidence)))
                except (TypeError, ValueError):
                    confidence = 0.8
                return SentimentResult(
                    rid=request.rid,
                    text=request.text,
                    sentiment=label,
                    confidence=confidence,
                    reason=str(data.get("reason", ""))[:80],
                    source="llm",
                )
        except Exception as exc:
            log(f"进程1 LLM 调用失败，改用词典规则：{exc}")

    label, confidence, reason = rule_sentiment(request.text)
    return SentimentResult(
        rid=request.rid,
        text=request.text,
        sentiment=label,
        confidence=confidence,
        reason=reason,
        source="rule",
    )


# --------------------------------------------------------------------------- #
# 进程2：鼓励 / 排解
# --------------------------------------------------------------------------- #
_STYLE_GUIDE = {
    "encourage": "用户此刻情绪偏正向。请真诚地肯定他/她，具体指出值得高兴的地方，"
    "再给一个能延续这份状态的小建议。语气温暖、有活力。",
    "comfort": "用户此刻情绪偏负面。请先共情和接纳，不评判、不说教；"
    "再温和地帮其梳理压力，给 1-2 个可以立刻执行的小步骤。语气温柔、稳定。",
    "neutral": "用户情绪偏中性。请自然回应，表达在倾听，邀请对方补充更多信息。语气平和。",
}

_SUPPORT_SYSTEM = """你是一位温暖、专业的中文心理陪伴者。
请使用第二人称，直接对用户说话；150 字以内；不要使用 Markdown 标题或列表；
不要说教，不要提及"情感识别""进程""策略"等系统内部细节。"""


def handle_support(request: SupportRequest) -> ReplyResult:
    settings = get_settings()

    if settings.llm_enabled:
        try:
            context = format_history(request.history)
            parts = []
            if context:
                parts.append(f"最近对话：\n{context}")
            parts.append(
                f"当前用户原话：{request.text}\n"
                f"情感识别结果：{request.sentiment}"
                f"（依据：{request.reason or '无'}）\n"
                f"回复策略：{_STYLE_GUIDE.get(request.style, _STYLE_GUIDE['neutral'])}\n"
                f"请直接给出回复。"
            )
            reply = complete_text(_SUPPORT_SYSTEM, "\n\n".join(parts))
            if reply:
                return ReplyResult(
                    rid=request.rid,
                    reply=reply,
                    style=request.style,
                    sentiment=request.sentiment,
                    source="llm",
                )
        except Exception as exc:
            log(f"进程2 LLM 调用失败，改用模板回复：{exc}")

    return ReplyResult(
        rid=request.rid,
        reply=fallback_reply(request.style, request.text),
        style=request.style,
        sentiment=request.sentiment,
        source="rule",
    )
