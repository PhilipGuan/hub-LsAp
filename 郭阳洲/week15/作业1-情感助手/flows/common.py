"""作业1 · 编排层：两框架共享的工具函数。

包含：兜底情感解析、短期/长期记忆的文本渲染、列表合并去重。
两套框架流水线（langgraph_flow / agents_flow）共同引用，
保证行为完全一致，便于公平对比。
"""
from __future__ import annotations

import json
import re

from core.entities import Memory, Message


# ---------------------------------------------------------------- 兜底解析

_FALLBACK_RULES = {
    "positive": ("positive", "正向", "正面", "积极"),
    "negative": ("negative", "负面", "消极"),
    "neutral": ("neutral", "中性", "中立"),
}


def extract_json_dict(text: str) -> dict | None:
    """从任意文本中提取 JSON dict（直接解析 → 去代码围栏 → 正则截取）。"""
    if not text:
        return None
    s = str(text).strip()

    # 1) 去掉 markdown 代码围栏
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()

    # 2) 直接解析
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except (ValueError, TypeError):
        pass

    # 3) 正则截取第一个 {...} 块
    match = re.search(r"\{.*\}", s, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            pass

    return None


def parse_sentiment_fallback(text: str) -> tuple[str, str]:
    """结构化输出失败时，从任意文本中兜底解析 (sentiment, intensity)。"""
    lowered = (text or "").lower()

    # 先尝试提取 JSON（模型偶尔把结果包在文本/围栏里）
    data = extract_json_dict(text)
    if data and "sentiment" in data:
        sentiment = str(data["sentiment"])
        intensity = str(data.get("intensity", "low"))
        if sentiment in _FALLBACK_RULES and intensity in ("high", "medium", "low"):
            return sentiment, intensity

    # 关键词兜底
    for label, keywords in _FALLBACK_RULES.items():
        if any(k in lowered for k in keywords):
            return label, "medium"
    return "neutral", "low"


# ---------------------------------------------------------------- 记忆渲染


def format_history(messages: list[Message]) -> str:
    """短期记忆渲染：用户/助手 多行文本。"""
    if not messages:
        return ""
    lines = [
        f"{'用户' if m.role == 'user' else '助手'}：{m.content}"
        for m in messages
    ]
    return "\n".join(lines)


def format_long_memory(memories: list[Memory]) -> str:
    """长期记忆渲染：情绪趋势 / 关键事件 / 主题 / 近况摘要。"""
    if not memories:
        return ""

    parts: list[str] = []
    by_key = {m.key: m.value for m in memories}

    trend = by_key.get("emotion_trend")
    if trend:
        parts.append(
            "情绪趋势：正向 {} 次、负面 {} 次、中性 {} 次".format(
                trend.get("positive", 0),
                trend.get("negative", 0),
                trend.get("neutral", 0),
            )
        )

    events = by_key.get("key_events", {}).get("items")
    if events:
        parts.append("关键事件：" + "；".join(events))

    topics = by_key.get("topics", {}).get("items")
    if topics:
        parts.append("常聊主题：" + "；".join(topics))

    summary = by_key.get("last_summary", {}).get("text")
    if summary:
        parts.append(f"用户近况：{summary}")

    return "\n".join(parts)


# ---------------------------------------------------------------- 列表合并


def merge_unique(existing: list[str], new: list[str], cap: int) -> list[str]:
    """合并去重（保序），并截断到 cap 上限。"""
    merged: list[str] = []
    for item in existing + new:
        item = (item or "").strip()
        if item and item not in merged:
            merged.append(item)
    return merged[:cap]
