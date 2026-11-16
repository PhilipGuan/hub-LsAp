"""文档业务服务（功能1 上传编排 + 功能2 状态查询）。

上传链路（F1-1 ~ F1-5）：校验 -> 判重 -> 存原件 -> 落库 PENDING -> 发 Kafka。
任一环节失败都体现在逐文件结果中，不中断整批。
"""
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BizError, ErrorCode
from app.core.logging import logger
from app.db.models import Document, DocumentStatus
from app.mq.kafka_producer import KafkaSendError, MQProducer
from app.mq.message_schemas import ParseTaskMessage
from app.schemas.document import UploadResultItem
from app.services.storage_service import BaseStorage
from app.utils.file_utils import new_id, sanitize_filename, sha256_hex, validate_pdf


def list_documents(
    db: Session,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[int, list[Document]]:
    query = db.query(Document)
    if status:
        try:
            query = query.filter(Document.status == DocumentStatus(status))
        except ValueError:
            raise BizError(ErrorCode.PARAM_ERROR, f"非法状态值: {status}")
    total = query.count()
    items = (
        query.order_by(Document.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return total, items


def get_document_or_404(db: Session, doc_id: str) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise BizError(ErrorCode.NOT_FOUND, f"文档不存在: {doc_id}", 404)
    return doc


async def upload_documents(
    files,
    db: Session,
    storage: BaseStorage,
    mq: MQProducer,
) -> list[UploadResultItem]:
    settings = get_settings()
    if not files:
        raise BizError(ErrorCode.PARAM_ERROR, "请至少选择一个文件")
    if len(files) > settings.max_upload_count:
        raise BizError(
            ErrorCode.TOO_MANY_FILES,
            f"单次最多上传 {settings.max_upload_count} 个文件",
        )

    results: list[UploadResultItem] = []
    for f in files:
        file_name = sanitize_filename(f.filename)
        try:
            data = await f.read()
            validate_pdf(file_name, data, settings)
            file_hash = sha256_hex(data)

            # F1-9 判重
            if not settings.allow_duplicate:
                dup = (
                    db.query(Document)
                    .filter(Document.file_hash == file_hash)
                    .first()
                )
                if dup is not None:
                    results.append(UploadResultItem(
                        file_name=file_name, status="duplicate",
                        code=ErrorCode.DUPLICATE_FORBIDDEN,
                        message=f"文件已存在（文档 {dup.id}，状态 {dup.status.value}）",
                    ))
                    continue

            # F1-3 存原件 + F1-4 落库
            file_id = new_id()
            storage_key = f"uploads/{file_id}.pdf"
            storage.save(storage_key, data)
            doc = Document(
                id=file_id,
                file_name=file_name,
                file_hash=file_hash,
                storage_path=storage_key,
                size=len(data),
                status=DocumentStatus.PENDING,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)

            # F1-5 发送 Kafka 消息；失败则任务置 FAILED
            msg = ParseTaskMessage(
                file_id=file_id,
                file_name=file_name,
                storage_path=storage_key,
                file_hash=file_hash,
            )
            try:
                mq.send_parse_task(msg)
            except KafkaSendError as exc:
                doc.status = DocumentStatus.FAILED
                doc.fail_reason = f"Kafka 消息发送失败: {exc}"
                doc.parse_finished_at = None
                db.commit()
                results.append(UploadResultItem(
                    file_name=file_name, document_id=file_id, status="error",
                    code=ErrorCode.KAFKA_SEND_FAILED, message=str(exc),
                ))
                continue

            logger.info(f"上传成功 doc_id={file_id} file={file_name} size={len(data)}B")
            results.append(UploadResultItem(
                file_name=file_name, document_id=file_id, status="ok",
            ))
        except BizError as exc:
            results.append(UploadResultItem(
                file_name=file_name, status="error", code=exc.code, message=exc.message,
            ))
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception(f"上传处理异常 file={file_name}: {exc}")
            results.append(UploadResultItem(
                file_name=file_name, status="error",
                code=ErrorCode.INTERNAL_ERROR, message=f"内部错误: {exc}",
            ))
    return results


def reparse_document(db: Session, doc_id: str, mq: MQProducer) -> Document:
    """失败任务重新解析（F2-5）：置回 PENDING 并重新入队。"""
    doc = get_document_or_404(db, doc_id)
    if doc.status != DocumentStatus.FAILED:
        raise BizError(ErrorCode.PARAM_ERROR, f"仅『解析失败』的任务可重新解析，当前状态: {doc.status.value}")

    doc.status = DocumentStatus.PENDING
    doc.fail_reason = None
    db.commit()

    msg = ParseTaskMessage(
        file_id=doc.id,
        file_name=doc.file_name,
        storage_path=doc.storage_path,
        file_hash=doc.file_hash,
    )
    try:
        mq.send_parse_task(msg)
    except KafkaSendError as exc:
        doc.status = DocumentStatus.FAILED
        doc.fail_reason = f"重新入队失败: {exc}"
        db.commit()
        raise BizError(ErrorCode.KAFKA_SEND_FAILED, str(exc), 503)
    logger.info(f"文档已重新入队 doc_id={doc_id}")
    return doc


def get_markdown_text(db: Session, storage: BaseStorage, doc_id: str) -> str:
    doc = get_document_or_404(db, doc_id)
    if doc.status != DocumentStatus.SUCCESS or not doc.markdown_path:
        raise BizError(ErrorCode.NOT_FOUND, f"文档尚未解析完成，无法预览: {doc_id}", 404)
    return storage.read_text(doc.markdown_path)
