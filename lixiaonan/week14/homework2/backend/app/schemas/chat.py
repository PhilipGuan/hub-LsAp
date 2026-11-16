"""问答相关 DTO（功能3）：请求参数、会话/消息输出、SSE 事件结构。"""
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

MAX_CHAT_IMAGES = 3
MAX_CHAT_IMAGE_MB = 10

# 允许的图片魔数
_IMAGE_MAGIC = {
    b"\x89PNG": "png",
    b"\xff\xd8\xff": "jpeg",
    b"RIFF": "webp",  # RIFF....WEBP
}


class ChatRequest(BaseModel):
    """问答请求（F3-1~F3-2）。

    images: base64 编码的用户图片（图问），不带 data: 前缀；
    document_ids: 限定检索范围的文档 id，为空表示全部文档。
    """
    query: str = Field(min_length=1, max_length=4000, description="用户问题")
    images: list[str] = Field(default_factory=list, max_length=MAX_CHAT_IMAGES)
    document_ids: list[str] = Field(default_factory=list)
    conversation_id: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)

    @field_validator("images")
    @classmethod
    def validate_images(cls, images: list[str]) -> list[str]:
        import base64 as _b64

        limit = MAX_CHAT_IMAGE_MB * 1024 * 1024
        for idx, b64 in enumerate(images):
            try:
                raw = _b64.b64decode(b64, validate=False)
            except Exception as exc:
                raise ValueError(f"第 {idx + 1} 张图片 base64 解码失败") from exc
            if len(raw) > limit:
                raise ValueError(f"第 {idx + 1} 张图片超过 {MAX_CHAT_IMAGE_MB}MB 上限")
            if not any(raw.startswith(m) for m in _IMAGE_MAGIC):
                raise ValueError(f"第 {idx + 1} 张图片格式不支持（仅 jpg/png/webp）")
        return images


class CitationItem(BaseModel):
    """回答引用的来源（F3-6）。"""
    chunk_type: str          # TEXT / IMAGE
    document_id: str
    file_name: str
    page_no: int | None = None
    image_url: str | None = None
    score: float | None = None


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: str
    text_content: str | None = None
    image_urls: list[str] = []
    citations: list[dict] = []
    created_at: datetime
