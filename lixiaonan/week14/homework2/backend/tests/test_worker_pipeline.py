"""功能1/2 集成测试：消费消息 -> PARSING -> 解析 -> 产物入库 -> SUCCESS 状态流转。"""
import pytest

from app.core.config import get_settings
from app.db.models import Chunk, ChunkType, Document, DocumentStatus
from app.mq.message_schemas import ParseTaskMessage
from worker.consumer import ParseWorker
from worker.parser.pymupdf_parser import PyMuPDFParser

from tests.conftest import make_pdf_bytes


@pytest.fixture()
def worker(_clean_db, storage, fake_mq):
    from app.db.base import SessionLocal

    return ParseWorker(
        settings=get_settings(),
        storage=storage,
        parser=PyMuPDFParser(get_settings()),
        session_factory=SessionLocal,  # 传会话工厂本身，Worker 内部按需创建
        mq=fake_mq,
    )


def _session_factory():
    from app.db.base import SessionLocal

    return SessionLocal()


def _make_pending_doc(storage, name: str = "流水线测试.pdf", pdf_bytes: bytes | None = None) -> ParseTaskMessage:
    """造一个 PENDING 文档并把 PDF 原件写入存储，返回解析任务消息。"""
    data = pdf_bytes if pdf_bytes is not None else make_pdf_bytes(pages=2)
    doc_id = "doc-w1"
    storage.save(f"uploads/{doc_id}.pdf", data)
    with _session_factory() as db:
        db.add(Document(id=doc_id, file_name=name, file_hash="hash-w1",
                        storage_path=f"uploads/{doc_id}.pdf", size=len(data),
                        status=DocumentStatus.PENDING))
        db.commit()
    return ParseTaskMessage(file_id=doc_id, file_name=name,
                            storage_path=f"uploads/{doc_id}.pdf", file_hash="hash-w1")


# ---------- F1-6 / F1-7 / F1-8：解析成功闭环 ----------
def test_parse_success_flow(worker, fake_mq, storage):
    msg = _make_pending_doc(storage)
    worker.process_message(msg.to_bytes())

    with _session_factory() as db:
        doc = db.get(Document, msg.file_id)
        assert doc.status == DocumentStatus.SUCCESS
        assert doc.fail_reason is None
        assert doc.page_count == 2
        assert doc.markdown_path == f"markdown/{doc.id}.md"
        chunks = db.query(Chunk).filter(Chunk.document_id == doc.id).all()
        assert len(chunks) >= 1
        assert all(c.chunk_type == ChunkType.TEXT for c in chunks)

    # Markdown 产物已写入存储且包含原文
    md = storage.read_text(doc.markdown_path)
    assert "Test page 1" in md
    # 成功路径不产生重试消息
    assert fake_mq.sent == []


# ---------- F1-8：图片抽取与 URL 改写 ----------
def test_parse_extracts_images_and_rewrites_urls(worker, storage):
    msg = _make_pending_doc(storage, pdf_bytes=make_pdf_bytes(pages=1, with_image=True))
    worker.process_message(msg.to_bytes())

    with _session_factory() as db:
        doc = db.get(Document, msg.file_id)
        assert doc.status == DocumentStatus.SUCCESS
        image_chunks = db.query(Chunk).filter(
            Chunk.document_id == doc.id, Chunk.chunk_type == ChunkType.IMAGE
        ).all()
        assert len(image_chunks) == 1
        assert image_chunks[0].image_url.startswith("/static/images/")
        assert image_chunks[0].image_url.endswith(".png")

    # Markdown 中本地引用已改写为 /static 可访问 URL
    md = storage.read_text(doc.markdown_path)
    assert image_chunks[0].image_url in md
    # 图片文件已落盘（key 可从 URL 还原）
    image_key = image_chunks[0].image_url.removeprefix("/static/")
    assert storage.exists(image_key)


# ---------- F1-6 / F2-3：失败重试（重新入队）与重试耗尽 ----------
def test_parse_failure_requeues(worker, fake_mq, storage, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)  # 跳过指数退避等待
    msg = _make_pending_doc(storage, pdf_bytes=b"%PDF-1.4 corrupt content")
    worker.process_message(msg.to_bytes())

    with _session_factory() as db:
        doc = db.get(Document, msg.file_id)
        assert doc.status == DocumentStatus.PENDING  # 等待重试，而非 FAILED
        assert "第 1 次解析失败" in doc.fail_reason
    assert len(fake_mq.sent) == 1
    assert fake_mq.sent[0].retry_count == 1  # retry_count+1 重新入队


def test_parse_failure_exhausted_marks_failed(worker, fake_mq, storage, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    msg = _make_pending_doc(storage, pdf_bytes=b"%PDF-1.4 corrupt content")
    exhausted = msg.model_copy(update={"retry_count": get_settings().kafka_max_retry})
    worker.process_message(exhausted.to_bytes())

    with _session_factory() as db:
        doc = db.get(Document, msg.file_id)
        assert doc.status == DocumentStatus.FAILED
        assert doc.fail_reason
        assert doc.parse_finished_at is not None
    assert fake_mq.sent == []  # 耗尽后不再入队


# ---------- 重解析幂等：不产生重复分块 ----------
def test_reparse_is_idempotent(worker, storage):
    msg = _make_pending_doc(storage, pdf_bytes=make_pdf_bytes(pages=1))
    worker.process_message(msg.to_bytes())
    worker.process_message(msg.to_bytes())  # 同一消息消费两次（重复投递场景）

    with _session_factory() as db:
        chunks = db.query(Chunk).filter(Chunk.document_id == msg.file_id).all()
        assert len(chunks) == 1  # 旧块先清空，不会翻倍


# ---------- 非法消息直接跳过 ----------
def test_invalid_message_skipped(worker, fake_mq, storage):
    worker.process_message(b"not-json")
    with _session_factory() as db:
        assert db.query(Document).count() == 0
    assert fake_mq.sent == []


# ---------- F2-4：PARSING 超时兜底 ----------
def _make_stale_doc(storage, fail_reason: str | None) -> str:
    from datetime import timedelta

    from app.db.models import utcnow

    doc_id = "doc-stale"
    storage.save(f"uploads/{doc_id}.pdf", make_pdf_bytes(pages=1))
    with _session_factory() as db:
        db.add(Document(id=doc_id, file_name="stale.pdf", file_hash="hash-stale",
                        storage_path=f"uploads/{doc_id}.pdf", size=1,
                        status=DocumentStatus.PARSING,
                        parse_started_at=utcnow() - timedelta(hours=1),
                        fail_reason=fail_reason))
        db.commit()
    return doc_id


def test_stale_parsing_requeued_once(worker, fake_mq, storage, settings):
    doc_id = _make_stale_doc(storage, fail_reason=None)
    n = worker.requeue_stale_parsing()
    assert n == 1
    assert len(fake_mq.sent) == 1
    assert fake_mq.sent[0].file_id == doc_id
    with _session_factory() as db:
        doc = db.get(Document, doc_id)
        assert doc.status == DocumentStatus.PENDING
        assert "超时" in doc.fail_reason


def test_stale_after_retry_marks_failed(worker, fake_mq, storage):
    doc_id = _make_stale_doc(storage, fail_reason="解析超时（>30 分钟），已自动重新入队")
    worker.requeue_stale_parsing()
    with _session_factory() as db:
        doc = db.get(Document, doc_id)
        assert doc.status == DocumentStatus.FAILED
        assert "手动重新解析" in doc.fail_reason
    assert fake_mq.sent == []  # 第二次超时不再入队，避免死循环


def test_stale_check_disabled(worker, fake_mq, storage, monkeypatch):
    monkeypatch.setattr(settings := get_settings(), "stale_parsing_minutes", 0)
    _make_stale_doc(storage, fail_reason=None)
    assert worker.requeue_stale_parsing() == 0
    assert fake_mq.sent == []
