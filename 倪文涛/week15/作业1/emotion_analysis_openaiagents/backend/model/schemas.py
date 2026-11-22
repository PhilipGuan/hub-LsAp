from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

Emotion = Literal["positive", "negative", "neutral"]
AgentName = Literal["positive_agent", "negative_agent", "neutral_agent"]
InputText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
ReplyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class MessageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(MessageModel):
    message: InputText


class UserInput(MessageModel):
    request_id: UUID
    user_input: InputText


class EmotionLabel(MessageModel):
    emotion: Emotion


class Classification(UserInput):
    emotion: Emotion


class SuccessResult(MessageModel):
    request_id: UUID
    status: Literal["success"] = "success"
    emotion: Emotion
    agent: AgentName
    reply: ReplyText

    @model_validator(mode="after")
    def matching_agent(self):
        if self.agent != f"{self.emotion}_agent":
            raise ValueError("回复 Agent 与情感标签不匹配")
        return self


class ErrorResult(MessageModel):
    request_id: UUID
    status: Literal["error"] = "error"
    stage: Literal["classification", "response", "backend"]
    code: str
    message: str


Result = Annotated[SuccessResult | ErrorResult, Field(discriminator="status")]
result_adapter = TypeAdapter(Result)
