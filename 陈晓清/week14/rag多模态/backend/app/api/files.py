# -*- coding: utf-8 -*-
"""文件上传、列表、删除、重新解析。"""

from __future__ import annotations

import shutil
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.config import (
    MAX_UPLOAD_MB,
    PROCESSED_DIR,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PARSING,
    STATUS_PENDING,
    UPLOAD_DIR,
    ensure_dirs,
)
from ..core.db import get_db
from ..orm import File as FileRecord
from ..schemas import (
    FileItem,
    FileListResponse,
    RejectedItem,
    SummaryResponse,
    UploadItem,
    UploadResponse,
)
from ..services import queue, vectorstore
from ..worker.tasks import process_file

router = APIRouter(prefix="/files", tags=["files"])


def _get_or_404(db: Session, file_id: int) -> FileRecord:
    record = db.query(FileRecord).filter(FileRecord.id == file_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return record


def _cleanup_vectors(file_id: int) -> None:
    try:
        vectorstore.delete_file(file_id)
    except Exception:
        pass


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_files(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    ensure_dirs()
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个 PDF 文件")

    items: list[UploadItem] = []
    rejected: list[RejectedItem] = []

    for upload in files:
        name = Path(upload.filename or "未命名.pdf").name
        if not name.lower().endswith(".pdf"):
            rejected.append(RejectedItem(filename=name, reason="只支持 PDF"))
            continue

        content = await upload.read()
        if not content:
            rejected.append(RejectedItem(filename=name, reason="文件为空"))
            continue
        if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
            rejected.append(RejectedItem(filename=name, reason=f"超过 {MAX_UPLOAD_MB}MB"))
            continue

        stored_path = UPLOAD_DIR / f"{uuid.uuid4().hex}.pdf"
        stored_path.write_bytes(content)

        record = FileRecord(
            filename=name,
            stored_path=str(stored_path),
            size_bytes=len(content),
            status=STATUS_PENDING,
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        queued = queue.enqueue_file(record.id, record.filename, record.stored_path)
        if queued:
            record.kafka_queued = True
            db.commit()

        items.append(
            UploadItem(
                id=record.id,
                filename=record.filename,
                status=record.status,
                status_text=record.status_text,
                kafka_queued=bool(record.kafka_queued),
            )
        )

    if not items and rejected:
        return UploadResponse(items=[], rejected=rejected)
    return UploadResponse(items=items, rejected=rejected)


@router.get("", response_model=FileListResponse)
def list_files(
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(FileRecord)
    if status:
        query = query.filter(FileRecord.status == status)

    total = query.count()
    rows = (
        query.order_by(FileRecord.created_at.desc(), FileRecord.id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return FileListResponse(
        total=total,
        items=[FileItem.model_validate(row) for row in rows],
    )


@router.get("/summary", response_model=SummaryResponse)
def summary(db: Session = Depends(get_db)):
    counts = dict(
        db.query(FileRecord.status, func.count(FileRecord.id))
        .group_by(FileRecord.status)
        .all()
    )
    chunks = db.query(func.coalesce(func.sum(FileRecord.chunk_count), 0)).scalar() or 0
    return SummaryResponse(
        pending=counts.get(STATUS_PENDING, 0),
        parsing=counts.get(STATUS_PARSING, 0),
        done=counts.get(STATUS_DONE, 0),
        failed=counts.get(STATUS_FAILED, 0),
        total=sum(counts.values()),
        chunks=int(chunks),
    )


@router.get("/{file_id}", response_model=FileItem)
def get_file(file_id: int, db: Session = Depends(get_db)):
    return FileItem.model_validate(_get_or_404(db, file_id))


@router.delete("/{file_id}", status_code=204)
def delete_file(file_id: int, db: Session = Depends(get_db)):
    record = _get_or_404(db, file_id)
    _cleanup_vectors(file_id)

    try:
        Path(record.stored_path).unlink(missing_ok=True)
    except OSError:
        pass
    shutil.rmtree(PROCESSED_DIR / str(file_id), ignore_errors=True)

    db.delete(record)
    db.commit()
    return None


@router.post("/{file_id}/reparse", status_code=202)
def reparse(file_id: int, db: Session = Depends(get_db)):
    record = _get_or_404(db, file_id)
    if record.status == STATUS_PARSING:
        raise HTTPException(status_code=409, detail="文件正在解析中")

    _cleanup_vectors(file_id)
    record.status = STATUS_PENDING
    record.error = None
    record.chunk_count = 0
    record.page_count = 0
    record.kafka_queued = queue.enqueue_file(record.id, record.filename, record.stored_path)
    db.commit()
    return {"id": record.id, "status": record.status, "kafka_queued": bool(record.kafka_queued)}


@router.post("/{file_id}/process", status_code=202)
def process_now(file_id: int, db: Session = Depends(get_db)):
    record = _get_or_404(db, file_id)
    if record.status == STATUS_PARSING:
        raise HTTPException(status_code=409, detail="文件正在解析中")

    queued = queue.enqueue_file(record.id, record.filename, record.stored_path)
    if not queued:
        threading.Thread(target=process_file, args=(record.id,), daemon=True).start()
    return {"id": record.id, "kafka_queued": queued}
