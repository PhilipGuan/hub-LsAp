"""共享消息契约。

三个进程之间只通过本文件中的模型交换数据，Kafka 消息字段不允许在各自进程内私自扩展。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Kafka topic 名称（DLQ 为毒消息隔离队列）
TOPIC_CHAT_REQUEST = "chat.request"
TOPIC_SENTIMENT_RESULT = "sentiment.result"
TOPIC_CHAT_RESPONSE = "chat.response"
TOPIC_SENTIMENT_RESULT_DLQ = "sentiment.result.dlq"
TOPIC_CHAT_RESPONSE_DLQ = "chat.response.dlq"


class KafkaModel(BaseModel):
    """所有 Kafka 消息的基类，统一序列化出口。"""

    def to_kafka(self) -> bytes:
        return self.model_dump_json().encode("utf-8")


class ChatRequest(KafkaModel):
    """后端 -> 情感识别进程。"""

    request_id: str
    session_id: str
    user_id: str
    message: str
    clarification_round: int = Field(default=0, ge=0)
    previous_clarification: str | None = None


class SentimentResult(KafkaModel):
    """情感识别进程 -> 响应进程。"""

    request_id: str
    session_id: str
    # 原样透传用户输入，供响应进程的 Agent 看到原文
    message: str
    sentiment: Literal["positive", "negative"]
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_round: int = Field(default=0, ge=0)


class ChatResponse(KafkaModel):
    """响应进程 -> chat.response，用于审计 / 幂等 / 重放。"""

    request_id: str
    session_id: str
    type: Literal["final", "clarification"]
    reply: str
    sentiment: Literal["positive", "negative"]
    confidence: float = Field(ge=0.0, le=1.0)
