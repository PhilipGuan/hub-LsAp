import hashlib
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..kafka_client import publish_parse_task, publish_status
from ..models import Document, utcnow
from ..schemas import DocumentOut
from ..services.es_store import delete_document as es_delete

router = APIRouter(prefix="/api/documents", tags=["documents"])
settings = get_settings()
log = logging.getLogger(__name__)


def _to_out(doc: Document) -> DocumentOut:
    return DocumentOut.model_validate(doc)


@router.post("", response_model=list[DocumentOut])
async def upload_documents(files: list[UploadFile], db: Session = Depends(get_db)):
    if not files:
        raise HTTPException(status_code=400, detail="没有选择文件")

    created: list[Document] = []
    limit = settings.max_upload_mb * 1024 * 1024

    for upload in files:
        name = Path(upload.filename or "unnamed.pdf").name
        if not name.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"{name} 不是 PDF 文件")
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=400, detail=f"{name} 是空文件")
        if len(data) > limit:
            raise HTTPException(
                status_code=400, detail=f"{name} 超过 {settings.max_upload_mb}MB 限制"
            )

        sha = hashlib.sha256(data).hexdigest()
        existing = db.scalar(select(Document).where(Document.sha256 == sha))
        if existing:
            created.append(existing)
            continue

        doc = Document(
            filename=name,
            size=len(data),
            sha256=sha,
            status="pending",
            file_path="",
        )
        db.add(doc)
        db.flush()

        target = settings.upload_dir / f"{doc.id}.pdf"
        target.write_bytes(data)
        doc.file_path = str(target)
        doc.updated_at = utcnow()
        db.commit()
        db.refresh(doc)

        publish_parse_task(doc.id, doc.file_path, doc.filename)
        publish_status(
            {
                "type": "doc_status",
                "doc_id": doc.id,
                "filename": doc.filename,
                "status": doc.status,
            }
        )
        created.append(doc)

    return [_to_out(d) for d in created]


@router.get("", response_model=list[DocumentOut])
def list_documents(status_filter: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Document).order_by(Document.created_at.desc())
    if status_filter:
        stmt = stmt.where(Document.status == status_filter)
    return [_to_out(d) for d in db.scalars(stmt).all()]


@router.get("/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: str, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return _to_out(doc)


@router.post("/{doc_id}/reparse", response_model=DocumentOut)
def reparse(doc_id: str, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    doc.status = "pending"
    doc.attempts = 0
    doc.error = None
    doc.updated_at = utcnow()
    db.commit()
    db.refresh(doc)

    publish_parse_task(doc.id, doc.file_path, doc.filename)
    publish_status(
        {
            "type": "doc_status",
            "doc_id": doc.id,
            "filename": doc.filename,
            "status": "pending",
        }
    )
    return _to_out(doc)


@router.delete("/{doc_id}")
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    try:
        es_delete(doc_id)
    except Exception:  # noqa: BLE001
        log.exception("delete es chunks failed")

    Path(doc.file_path).unlink(missing_ok=True)
    shutil.rmtree(settings.parsed_dir / doc_id, ignore_errors=True)
    db.delete(doc)
    db.commit()
    return {"ok": True}
