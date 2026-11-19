"""情绪识别和聊天接口使用的数据模型。"""

from typing import Annotated, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import TypedDict


Sentiment = Literal["positive", "negative", "neutral"]
_PositiveEmotion = Literal["joy", "excitement", "gratitude"]
_NegativeEmotion = Literal["sadness", "anxiety", "anger", "dejection", "disappointment"]
_NeutralEmotion = Literal["calm", "neutral"]
EmotionLabel = Literal[_PositiveEmotion, _NegativeEmotion, _NeutralEmotion]
AgentStatus = Literal["success", "failed", "skipped"]
NonBlankText = Annotated[str, StringConstraints(strict=True, pattern=r"\S")]

EMOTIONS_BY_SENTIMENT = {
    "positive": get_args(_PositiveEmotion),
    "negative": get_args(_NegativeEmotion),
    "neutral": get_args(_NeutralEmotion),
}


class EmotionResult(BaseModel):
    """Agent 1 对当前消息的情绪识别结果。"""

    model_config = ConfigDict(extra="forbid")

    sentiment: Sentiment = Field(description="正面、负面或中性的情绪倾向。")
    emotion: EmotionLabel = Field(description="与情绪倾向一致的细分情绪标签。")

    @model_validator(mode="after")
    def validate_emotion_matches_sentiment(self) -> "EmotionResult":
        if self.emotion not in EMOTIONS_BY_SENTIMENT[self.sentiment]:
            raise ValueError("细分情绪标签与情绪倾向不匹配")
        return self


class HistoryMessage(TypedDict):
    """会话中保存的一条用户消息或助手回复。"""

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """发起新会话或继续已有会话的单轮请求。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    message: NonBlankText
    session_id: Optional[NonBlankText] = None
    debug: bool = False


class ChatDebugInfo(BaseModel):
    """调试模式下的 Agent 执行状态和可用的情绪结果。"""

    emotion_agent_status: AgentStatus
    response_agent_status: AgentStatus
    emotion: Optional[EmotionResult] = None


class ChatResponse(BaseModel):
    """一次性返回的完整聊天回复。"""

    session_id: NonBlankText
    reply: NonBlankText
    debug: Optional[ChatDebugInfo] = None
