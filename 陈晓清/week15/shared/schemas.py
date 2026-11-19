"""跨进程与 API 层共用的 Pydantic 数据模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Sentiment = Literal["positive", "negative"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RawInputMessage(BaseModel):
    """FastAPI 生产到 raw_input 的用户输入。"""

    request_id: str
    text: str
    created_at: datetime = Field(default_factory=utc_now)


class SentimentMessage(BaseModel):
    """进程 1 生产到 sentiment_result 的情感识别结果。"""

    request_id: str
    type: Literal["sentiment"] = "sentiment"
    text: str
    sentiment: Sentiment
    created_at: datetime = Field(default_factory=utc_now)


class ReplyMessage(BaseModel):
    """进程 2 生产到 sentiment_result 的最终回复。"""

    request_id: str
    type: Literal["reply"] = "reply"
    sentiment: Sentiment
    reply: str
    created_at: datetime = Field(default_factory=utc_now)


class ChatRequest(BaseModel):
    """POST /api/chat 请求体。"""

    message: str = Field(min_length=1)


class ChatResponse(BaseModel):
    """POST /api/chat 成功响应。"""

    request_id: str
    input: str
    sentiment: Sentiment
    reply: str
