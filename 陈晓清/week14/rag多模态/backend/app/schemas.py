# -*- coding: utf-8 -*-
"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UploadItem(BaseModel):
    id: int
    filename: str
    status: str
    status_text: str
    kafka_queued: bool


class RejectedItem(BaseModel):
    filename: str
    reason: str


class UploadResponse(BaseModel):
    items: list[UploadItem] = Field(default_factory=list)
    rejected: list[RejectedItem] = Field(default_factory=list)


class FileItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    size_bytes: int = 0
    status: str
    status_text: str
    page_count: int = 0
    chunk_count: int = 0
    parse_seconds: float = 0.0
    error: str | None = None
    kafka_queued: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class FileListResponse(BaseModel):
    total: int
    items: list[FileItem]


class SummaryResponse(BaseModel):
    pending: int = 0
    parsing: int = 0
    done: int = 0
    failed: int = 0
    total: int = 0
    chunks: int = 0


class ChatSource(BaseModel):
    file_id: int | None = None
    file_name: str = ""
    page: int = 0
    score: float | None = None
    snippet: str = ""
    images: list[str] = Field(default_factory=list)


class ChatTimings(BaseModel):
    retrieve_ms: int = 0
    llm_ms: int = 0


class ChatResponse(BaseModel):
    answer: str
    used_image: bool = False
    sources: list[ChatSource] = Field(default_factory=list)
    timings: ChatTimings = Field(default_factory=ChatTimings)


class HealthItem(BaseModel):
    ok: bool
    detail: str = ""


class HealthResponse(BaseModel):
    api: HealthItem
    kafka: HealthItem
    milvus: HealthItem
    llm: HealthItem
    mineru: HealthItem


class StatsResponse(BaseModel):
    files: int = 0
    chunks: int = 0
    milvus_rows: int = 0
    disk_free_gb: float = 0.0
