"""前后端数据契约。

所有跨进程传输的结构都定义在这里，LangGraph 的节点也只使用这些模型，
这样接口字段、结构化输出 schema 和前端展示可以保持一致。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

EmotionLabel = Literal["positive", "negative", "neutral"]
Strategy = Literal["encourage", "soothe", "neutral_reply"]


class EmotionResult(BaseModel):
    """进程1（情感识别）的结构化输出。"""

    label: EmotionLabel = Field(
        description="整体情感倾向：positive=正向，negative=负面，neutral=中性"
    )
    emotion: str = Field(
        description=(
            "更细的情绪标签，使用英文 snake_case，例如 "
            "joy / gratitude / pride / sadness / anxiety / anger / "
            "tiredness / loneliness / disappointment / neutral"
        )
    )
    confidence: float = Field(ge=0.0, le=1.0, description="判断置信度，0~1")
    intensity: int = Field(ge=1, le=5, description="情绪强度，1 最弱、5 最强")
    reason: str = Field(description="用一句中文说明判断依据，不要复述整段话")
    keywords: list[str] = Field(
        default_factory=list,
        description="用户原话中触发判断的关键词或短语，最多 5 个",
    )
    risk: bool = Field(
        default=False,
        description="是否出现自伤、轻生、极度绝望等危机信号",
    )

    @field_validator("emotion")
    @classmethod
    def _normalize_emotion(cls, value: str) -> str:
        value = "_".join(str(value).strip().lower().split())
        return value or "neutral"

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str) -> str:
        value = " ".join(str(value).split()).strip()
        return value[:200] or "未给出判断依据"

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = " ".join(str(value).split()).strip()
            if text and text not in cleaned:
                cleaned.append(text[:30])
        return cleaned[:5]


class ReplyContent(BaseModel):
    """进程2（回复生成）的结构化输出；strategy 由代码决定，避免模型改路由。"""

    reply_text: str = Field(description="直接发给用户的回复正文，2~5 句自然中文")
    suggestions: list[str] = Field(
        default_factory=list,
        description="可执行的短建议：负面情绪 2~3 条，其他情况可以为空",
    )

    @field_validator("reply_text")
    @classmethod
    def _clean_reply(cls, value: str) -> str:
        return str(value).strip()

    @field_validator("suggestions")
    @classmethod
    def _clean_suggestions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = " ".join(str(value).split()).strip().lstrip("-•· ").strip()
            if text and text not in cleaned:
                cleaned.append(text[:80])
        return cleaned[:3]


class SupportReply(BaseModel):
    """进程2 最终写入状态的回复。"""

    strategy: Strategy = Field(description="本轮回复分支")
    reply_text: str = Field(description="回复正文")
    suggestions: list[str] = Field(default_factory=list, description="可执行建议")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="用户本轮输入")
    thread_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="会话 ID；为空时由后端生成",
    )

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message 不能为空")
        return value


class ChatResponse(BaseModel):
    """POST /api/chat 的响应。"""

    thread_id: str
    message: str = Field(description="本轮用户输入（回显）")
    emotion: EmotionResult
    reply: SupportReply
    trace: list[str] = Field(default_factory=list, description="本轮节点执行轨迹")


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class HistoryResponse(BaseModel):
    thread_id: str
    messages: list[HistoryMessage] = Field(default_factory=list)
