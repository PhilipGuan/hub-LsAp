"""Request/response schemas for the HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=8000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    image_data_url: str | None = Field(default=None, max_length=16_000_000)
    doc_ids: list[str] | None = Field(default=None, max_length=50)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    session_id: str | None = Field(default=None, max_length=100)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value

    @field_validator("image_data_url")
    @classmethod
    def check_image_data_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("data:image/"):
            raise ValueError("image_data_url 必须是 data:image/... base64 数据")
        return value
