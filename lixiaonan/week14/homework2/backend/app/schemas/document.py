"""文档相关 DTO（§5）：上传逐文件结果、文档列表项。"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict

from app.db.models import DocumentStatus


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    file_name: str
    size: int
    status: DocumentStatus
    fail_reason: str | None = None
    page_count: int | None = None
    parser_name: str | None = None
    created_at: datetime
    parse_started_at: datetime | None = None
    parse_finished_at: datetime | None = None
    duration_seconds: float | None = None


class UploadResultItem(BaseModel):
    """批量上传的逐文件结果：status ∈ ok / duplicate / error。"""

    file_name: str
    document_id: str | None = None
    status: str
    code: int = 0
    message: str = "ok"
