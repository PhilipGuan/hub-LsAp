from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    size: int
    sha256: str
    page_count: int
    status: str
    attempts: int
    error: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    parsed_at: datetime | None


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: str
    content: str
    images_json: str = "[]"
    sources_json: str = "[]"
    created_at: datetime


class SessionCreate(BaseModel):
    title: str | None = None

