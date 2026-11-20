"""文档接口（功能1 + 功能2）。

- POST /api/v1/documents/upload          批量上传 PDF（F1-1~F1-5）
- GET  /api/v1/documents                 文档列表（F2-1，含状态供前端轮询）
- GET  /api/v1/documents/{doc_id}        文档详情（F2-3）
- GET  /api/v1/documents/{doc_id}/markdown  解析结果 Markdown 预览（F2-5）
- POST /api/v1/documents/{doc_id}/reparse   失败任务重新解析（F2-5）
"""
from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_mq, get_storage
from app.core.exceptions import BizError, ErrorCode
from app.db.models import DocumentStatus
from app.schemas.common import ok
from app.schemas.document import DocumentOut
from app.services import document_service
from app.services.storage_service import BaseStorage
from app.mq.kafka_producer import MQProducer

router = APIRouter()


@router.post("/upload")
async def upload_documents(
    files: list[UploadFile] = File(..., description="PDF 文件，可一次选择多个"),
    db: Session = Depends(get_db),
    storage: BaseStorage = Depends(get_storage),
    mq: MQProducer = Depends(get_mq),
):
    """批量上传 PDF（F1-1~F1-5）。返回逐文件处理结果。"""
    results = await document_service.upload_documents(files, db, storage, mq)
    return ok(results)


@router.get("")
def list_documents(
    status: str | None = Query(None, description="按状态过滤: PENDING/PARSING/SUCCESS/FAILED"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """文档列表（F2-1）：前端轮询该接口刷新解析状态。"""
    if status and status not in DocumentStatus.__members__:
        raise BizError(ErrorCode.PARAM_ERROR, f"非法状态值: {status}")
    total, items = document_service.list_documents(db, status, page, page_size)
    return ok({
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [DocumentOut.model_validate(d).model_dump(mode="json") for d in items],
    })


@router.get("/{doc_id}")
def get_document(doc_id: str, db: Session = Depends(get_db)):
    """文档详情（F2-3）。"""
    doc = document_service.get_document_or_404(db, doc_id)
    return ok(DocumentOut.model_validate(doc).model_dump(mode="json"))


@router.get("/{doc_id}/markdown")
def get_document_markdown(
    doc_id: str,
    db: Session = Depends(get_db),
    storage: BaseStorage = Depends(get_storage),
):
    """解析结果 Markdown 预览（F2-5）。图片引用已被改写为可访问 URL。"""
    text = document_service.get_markdown_text(db, storage, doc_id)
    return ok({"document_id": doc_id, "markdown": text})


@router.post("/{doc_id}/reparse")
def reparse_document(
    doc_id: str,
    db: Session = Depends(get_db),
    mq: MQProducer = Depends(get_mq),
):
    """失败任务重新解析（F2-5，可选功能）。"""
    doc = document_service.reparse_document(db, doc_id, mq)
    return ok(DocumentOut.model_validate(doc).model_dump(mode="json"))
