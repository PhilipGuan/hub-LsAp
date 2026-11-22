# -*- coding: utf-8 -*-
"""files 表：文件元数据与解析状态。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base

from .core.config import STATUS_TEXT

Base = declarative_base()


class File(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(1000), nullable=False)
    size_bytes = Column(Integer, default=0)

    status = Column(String(20), nullable=False, default="pending", index=True)
    page_count = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)
    parse_seconds = Column(Float, default=0.0)
    error = Column(Text)
    kafka_queued = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<File id={self.id} name={self.filename} status={self.status}>"
