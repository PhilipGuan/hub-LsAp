"""共享的情感识别（进程1）与模板回复（进程2 的离线兜底）。

设计：
- 配置了 OPENAI_API_KEY 时，调用 LLM 做情感识别；
- 未配置时，退化为基于关键词的规则判断，保证离线也能跑通示例。
"""
from __future__ import annotations

import json
from typing import Literal

Sentiment = Literal["positive", "negative", "neutral"]

_POSITIVE_WORDS = {
    "开心", "高兴", "快乐", "喜欢", "满意", "棒", "赞", "优秀", "成功",
    "兴奋", "期待", "幸福", "顺利", "感谢", "太好了", "不错", "舒服",
    "great", "happy", "love", "good", "awesome", "excellent",
    "amazing", "wonderful", "fantastic", "nice", "success",
}
_NEGATIVE_WORDS = {
    "难过", "伤心", "生气", "愤怒", "沮丧", "失望", "焦虑", "压力",
    "累", "烦", "讨厌", "糟糕", "失败", "痛苦", "害怕", "委屈",
    "不开心", "难受", "崩溃", "绝望", "郁闷", "烦躁",
    "sad", "angry", "bad", "terrible", "awful", "depressed",
    "anxious", "stressed", "upset", "frustrated", "tired", "hate",
}


def classify_rule(text: str) -> Sentiment:
    """离线兜底：按正/负关键词命中数判断情感。"""
    t = (text or "").lower()
    pos = sum(1 for w in _POSITIVE_WORDS if w in t)
    neg = sum(1 for w in _NEGATIVE_WORDS if w in t)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


_SENTIMENT_SYSTEM = (
    "你是情感识别助手。分析用户输入的情感倾向，"
    '只输出一个 JSON 对象：{"sentiment": "positive"|"negative"|"neutral"}，不要输出任何其他内容。'
)


async def classify_by_llm(text: str, *, client, model: str) -> Sentiment:
    """调用 OpenAI 兼容接口做情感识别（进程1 的 LLM 版本）。"""
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SENTIMENT_SYSTEM},
            {"role": "user", "content": text},
        ],
        temperature=0,
    )
    content = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(content)
        label = str(data.get("sentiment", "")).lower()
        if label in ("positive", "negative", "neutral"):
            return label  # type: ignore[return-value]
    except (ValueError, TypeError):
        pass
    low = content.lower()
    if "positive" in low:
        return "positive"
    if "negative" in low:
        return "negative"
    return classify_rule(text)


def template_response(sentiment: Sentiment) -> str:
    """离线兜底：根据情感给出固定模板回复（进程2）。"""
    if sentiment == "positive":
        return "听到你这么说真替你开心！继续保持这份好心情，好运会一直伴随着你。"
    if sentiment == "negative":
        return "抱抱你，我知道你现在一定很不好受。情绪会过去的，我会一直在这里陪着你，我们慢慢面对。"
    return "我听到了。无论你现在感觉如何，都值得被认真对待，愿你能照顾好自己。"
