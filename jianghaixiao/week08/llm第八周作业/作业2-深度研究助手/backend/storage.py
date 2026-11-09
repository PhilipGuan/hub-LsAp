"""JSON 文件存储。每个研究主题一个文件。"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .models import ResearchRecord, Status

_LOCK = threading.Lock()


def _path(research_id: str) -> Path:
    config.ensure_data_dir()
    return config.DATA_DIR / f"{research_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(research_id: str, topic: str) -> ResearchRecord:
    now = _now()
    record = ResearchRecord(
        research_id=research_id,
        topic=topic,
        status=Status.pending,
        created_at=now,
        updated_at=now,
    )
    save(record)
    return record


def save(record: ResearchRecord) -> None:
    record.updated_at = _now()
    target = _path(record.research_id)
    temp = target.with_suffix(".tmp")
    with _LOCK:
        temp.write_text(
            record.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temp.replace(target)


def get(research_id: str) -> ResearchRecord | None:
    target = _path(research_id)
    if not target.exists():
        return None
    return ResearchRecord.model_validate_json(target.read_text(encoding="utf-8"))


def list_all() -> list[ResearchRecord]:
    config.ensure_data_dir()
    records = [
        ResearchRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in config.DATA_DIR.glob("*.json")
    ]
    return sorted(records, key=lambda item: item.created_at, reverse=True)


def update_status(
    research_id: str,
    status: Status,
    error: str | None = None,
) -> None:
    record = get(research_id)
    if record is None:
        return
    record.status = status
    if error:
        record.error = error
    save(record)
