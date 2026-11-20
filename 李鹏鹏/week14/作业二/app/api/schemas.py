"""请求/响应模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(default="", max_length=4000, description="用户问题")
    session_id: str | None = Field(default=None, description="会话 ID，不传则新建")
    doc_ids: list[str] = Field(default_factory=list, description="限定检索的文档范围")
    image: str | None = Field(
        default=None,
        description="随问题上传的图片，data URL（data:image/...;base64,...）",
    )


class ReparseRequest(BaseModel):
    backend: str | None = Field(default=None, description="可选的解析后端覆盖：mineru / lite")


class DocumentQuery(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
