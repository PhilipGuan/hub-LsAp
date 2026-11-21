"""作业1 · 核心层：pydantic 实体对象（全项目唯一实体源）。

存储层、编排层、接口层均引用本模块的实体，禁止散落 dict/TypedDict。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 枚举


class Sentiment(str, Enum):
    """情感三分类（进程1 输出空间）。"""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class Intensity(str, Enum):
    """情绪强度。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------- 长期记忆 key 常量

MEMORY_KEY_EMOTION_TREND = "emotion_trend"   # 情绪趋势计数（程序化维护）
MEMORY_KEY_EVENTS = "key_events"             # 关键事件（LLM 提炼）
MEMORY_KEY_TOPICS = "topics"                 # 常聊主题（LLM 提炼）
MEMORY_KEY_SUMMARY = "last_summary"          # 滚动摘要（LLM 生成）


# ---------------------------------------------------------------- LLM 结构化输出实体


class SentimentResult(BaseModel):
    """进程1（情感识别）的结构化输出。"""

    sentiment: Sentiment
    intensity: Intensity = Intensity.LOW


class MemoryUpdateResult(BaseModel):
    """记忆回写 Agent 的结构化输出。"""

    key_events: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------- 领域实体


class Session(BaseModel):
    """会话（多会话隔离的基本单位）。"""

    session_id: str
    title: str = "新会话"
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    """消息（短期记忆的基本单位）。"""

    session_id: str
    role: Literal["user", "assistant"]
    content: str
    sentiment: Sentiment | None = None
    id: int | None = None
    created_at: datetime | None = None


class Memory(BaseModel):
    """长期记忆（结构化键值，per session）。"""

    session_id: str
    key: str
    value: dict
    updated_at: datetime | None = None


# ---------------------------------------------------------------- API 实体


class ChatRequest(BaseModel):
    """POST /chat 入参。session_id 为空表示新建会话。"""

    session_id: str | None = None
    text: str


class ChatResponse(BaseModel):
    """POST /chat 出参。"""

    session_id: str
    framework: str = ""
    sentiment: Sentiment
    intensity: Intensity
    reply: str
    memory_summary: str | None = None


class SessionDetail(BaseModel):
    """GET /sessions/{id} 出参：会话 + 历史 + 长期画像。"""

    session: Session
    messages: list[Message]
    memories: list[Memory]