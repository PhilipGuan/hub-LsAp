"""功能1 接口测试：多文件上传、类型/大小校验、hash 判重、Kafka 消息发送。"""
import pytest

from app.db.models import Document, DocumentStatus
from app.mq.message_schemas import ParseTaskMessage

from tests.conftest import make_pdf_bytes


# ---------- F1-1 / F1-3 / F1-4 / F1-5：批量上传成功链路 ----------
def test_upload_batch_ok(client, fake_mq, storage, settings):
    files = [
        ("files", ("doc_a.pdf", make_pdf_bytes(pages=1), "application/pdf")),
        ("files", ("doc_b.pdf", make_pdf_bytes(pages=2), "application/pdf")),
    ]
    resp = client.post("/api/v1/documents/upload", files=files)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    results = body["data"]
    assert [r["status"] for r in results] == ["ok", "ok"]
    doc_ids = [r["document_id"] for r in results]

    # F1-4：落库 PENDING；F1-3：原件已存；F1-5：消息已入队且字段完整
    from app.db.base import SessionLocal

    with SessionLocal() as db:
        for doc_id, r in zip(doc_ids, results):
            doc = db.get(Document, doc_id)
            assert doc is not None
            assert doc.status == DocumentStatus.PENDING
            assert doc.file_hash  # SHA-256 已记录
            assert storage.exists(doc.storage_path)

    assert len(fake_mq.sent) == 2
    for msg, doc_id in zip(fake_mq.sent, doc_ids):
        assert isinstance(msg, ParseTaskMessage)
        assert msg.file_id == doc_id
        assert msg.storage_path == f"uploads/{doc_id}.pdf"
        assert msg.retry_count == 0
        assert msg.task_id


def _session():
    from app.db.base import SessionLocal

    return SessionLocal()


# ---------- F1-2：类型 / 魔数 / 大小 / 数量校验（错误体现在逐文件结果中） ----------
def test_upload_rejects_non_pdf(client, fake_mq):
    resp = client.post("/api/v1/documents/upload", files=[
        ("files", ("note.txt", b"hello", "text/plain")),
    ])
    body = resp.json()
    assert body["code"] == 0  # 批量接口不中断，错误在逐文件结果里
    item = body["data"][0]
    assert item["status"] == "error"
    assert item["code"] == 1003  # FILE_TYPE_INVALID
    assert fake_mq.sent == []  # 非法文件不应入队


def test_upload_rejects_bad_magic(client, fake_mq):
    # 缺少 %PDF- 魔数：上传阶段即拒绝；「有魔数但内容损坏」由 Worker 解析阶段兜底
    resp = client.post("/api/v1/documents/upload", files=[
        ("files", ("fake.pdf", b"NOT-PDF-AT-ALL" + b"x" * 64, "application/pdf")),
    ])
    item = resp.json()["data"][0]
    assert item["status"] == "error"
    assert item["code"] == 1003


def test_upload_rejects_oversize(client, fake_mq, settings):
    big = b"%PDF-1.4" + b"\x00" * (settings.max_upload_size_mb * 1024 * 1024 + 1024)
    resp = client.post("/api/v1/documents/upload", files=[
        ("files", ("big.pdf", big, "application/pdf")),
    ])
    item = resp.json()["data"][0]
    assert item["status"] == "error"
    assert item["code"] == 1002  # FILE_TOO_LARGE
    assert "超过上限" in item["message"]
    assert fake_mq.sent == []


def test_upload_rejects_too_many_files(client, fake_mq, settings):
    files = [("files", (f"f{i}.pdf", make_pdf_bytes(), "application/pdf")) for i in range(settings.max_upload_count + 1)]
    resp = client.post("/api/v1/documents/upload", files=files)
    assert resp.json()["code"] == 1004  # TOO_MANY_FILES


# ---------- F1-5：Kafka 发送失败 -> 任务置 FAILED ----------
def test_upload_kafka_failure_marks_failed(client, fake_mq):
    fake_mq.fail_next = True
    resp = client.post("/api/v1/documents/upload", files=[
        ("files", ("doc.pdf", make_pdf_bytes(), "application/pdf")),
    ])
    result = resp.json()["data"][0]
    assert result["status"] == "error"
    assert result["code"] == 2001  # KAFKA_SEND_FAILED

    with _session() as db:
        doc = db.get(Document, result["document_id"])
        assert doc.status == DocumentStatus.FAILED
        assert "Kafka" in doc.fail_reason


# ---------- F1-9：内容 hash 判重 ----------
def test_duplicate_forbidden_when_configured(client, fake_mq, settings, monkeypatch):
    monkeypatch.setattr(settings, "allow_duplicate", False)
    data = make_pdf_bytes()
    first = client.post("/api/v1/documents/upload", files=[
        ("files", ("same.pdf", data, "application/pdf"))]).json()["data"][0]
    second = client.post("/api/v1/documents/upload", files=[
        ("files", ("same.pdf", data, "application/pdf"))]).json()["data"][0]
    assert first["status"] == "ok"
    assert second["status"] == "duplicate"
    assert second["code"] == 1005  # DUPLICATE_FORBIDDEN
    assert len(fake_mq.sent) == 1  # 重复文件不入队


def test_duplicate_allowed_by_default(client, fake_mq):
    data = make_pdf_bytes()
    for _ in range(2):
        client.post("/api/v1/documents/upload", files=[("files", ("same.pdf", data, "application/pdf"))])
    assert len(fake_mq.sent) == 2  # ALLOW_DUPLICATE=true（默认）：允许重复入库


# ---------- F2-5：失败任务重新解析 ----------
def test_reparse_only_failed(client, fake_mq):
    # SUCCESS 状态不可重解析
    with _session() as db:
        doc = Document(id="doc-succ", file_name="ok.pdf", file_hash="h1", storage_path="uploads/x.pdf",
                       size=1, status=DocumentStatus.SUCCESS)
        db.add(doc)
        db.commit()
    resp = client.post("/api/v1/documents/doc-succ/reparse")
    assert resp.json()["code"] == 1001

    # FAILED -> 重新入队并置回 PENDING
    with _session() as db:
        failed = Document(id="doc-fail", file_name="bad.pdf", file_hash="h2", storage_path="uploads/y.pdf",
                          size=1, status=DocumentStatus.FAILED, fail_reason="解析异常")
        db.add(failed)
        db.commit()
    resp = client.post("/api/v1/documents/doc-fail/reparse")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == DocumentStatus.PENDING.value
    assert len(fake_mq.sent) == 1
    assert fake_mq.sent[0].file_id == "doc-fail"


# ---------- F2-5：Markdown 预览 ----------
def test_markdown_preview(client, storage):
    with _session() as db:
        db.add(Document(id="doc-md", file_name="m.pdf", file_hash="h3", storage_path="uploads/m.pdf",
                        size=1, status=DocumentStatus.PENDING))
        db.commit()
    # 未解析完成 -> 404
    assert client.get("/api/v1/documents/doc-md/markdown").json()["code"] == 1006

    storage.write_text("markdown/doc-md.md", "# 解析结果\n正文")
    with _session() as db:
        doc = db.get(Document, "doc-md")
        doc.status = DocumentStatus.SUCCESS
        doc.markdown_path = "markdown/doc-md.md"
        db.commit()

    resp = client.get("/api/v1/documents/doc-md/markdown")
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["markdown"].startswith("# 解析结果")


# ---------- F2-1：列表与状态过滤 ----------
def test_list_documents_status_filter(client):
    with _session() as db:
        db.add(Document(id="d1", file_name="a.pdf", file_hash="h1", storage_path="u/1", size=1, status=DocumentStatus.SUCCESS))
        db.add(Document(id="d2", file_name="b.pdf", file_hash="h2", storage_path="u/2", size=1, status=DocumentStatus.FAILED))
        db.commit()

    body = client.get("/api/v1/documents").json()
    assert body["data"]["total"] == 2
    assert {i["status"] for i in body["data"]["items"]} == {"SUCCESS", "FAILED"}

    body = client.get("/api/v1/documents?status=FAILED").json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["file_name"] == "b.pdf"

    assert client.get("/api/v1/documents?status=BOGUS").json()["code"] == 1001


def test_document_detail_404(client):
    assert client.get("/api/v1/documents/no-such-doc").json()["code"] == 1006


# ---------- 消息格式（§3.1.3） ----------
def test_parse_task_message_roundtrip():
    msg = ParseTaskMessage(file_id="fid", file_name="f.pdf", storage_path="uploads/fid.pdf", file_hash="abc")
    restored = ParseTaskMessage.model_validate_json(msg.to_bytes())
    assert restored.file_id == "fid"
    assert restored.retry_count == 0
    assert restored.task_id == msg.task_id
