"""进程间消息协议。

所有消息都是 dataclass，可以通过 multiprocessing.Queue 跨进程传递（可 pickle）。
每条请求都带 rid（request id），方便后端把"哪条输入"和"哪条回复"对上。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

Sentiment = Literal["positive", "negative", "neutral"]
Style = Literal["encourage", "comfort", "neutral"]


def new_rid() -> str:
    return uuid.uuid4().hex


@dataclass
class UserRequest:
    """前端进程 -> 后端进程"""

    rid: str
    text: str
    session_id: str = "default"


@dataclass
class SentimentRequest:
    """后端进程 -> 进程1（情感识别）"""

    rid: str
    text: str


@dataclass
class SentimentResult:
    """进程1 -> 后端进程"""

    rid: str
    text: str
    sentiment: Sentiment
    confidence: float
    reason: str
    source: str = "llm"  # model / llm / rule


@dataclass
class SupportRequest:
    """后端进程 -> 进程2（鼓励 / 排解）

    history 是最近几轮对话，形状为 [{"user": ..., "assistant": ...}]；
    由后端按 session 维护，进程2 只用它组织上下文，不负责存储。
    """

    rid: str
    text: str
    sentiment: Sentiment
    reason: str = ""
    style: Style = "neutral"
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ReplyResult:
    """进程2 -> 后端进程 -> 前端进程"""

    rid: str
    reply: str
    style: str = ""
    sentiment: str = ""
    source: str = "llm"


@dataclass
class ErrorResult:
    """任意进程出错时的统一错误回包"""

    rid: str
    error: str
