"""端到端接口测试：上传 → 解析状态 → 预览 → 图文问答 → 历史。

依赖 conftest 中的环境变量（Kafka 关闭 + 内置解析器 + LLM_MOCK）。
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

from tests.pdf_factory import make_png, make_text_pdf

SAMPLE_LINES = [
    "Retrieval augmented generation combines document search with text generation.",
    "The ingestion service parses PDF files into text blocks, tables and figures.",
    "Kafka decouples upload from parsing so the HTTP request returns immediately.",
    "Figure 1 illustrates the pipeline from upload to the retrieval index.",
]


def _upload(client, path: Path):
    with path.open("rb") as handle:
        response = client.post(
            "/api/documents",
            files=[("files", (path.name, handle, "application/pdf"))],
        )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_terminal(client, doc_id: str, timeout: float = 40.0) -> dict:
    deadline = time.time() + timeout
    latest: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/documents/{doc_id}")
        assert response.status_code == 200, response.text
        latest = response.json()["item"]
        if latest["status"] in {"done", "failed"}:
            return latest
        time.sleep(0.15)
    raise AssertionError(f"解析超时，最后状态：{latest}")


def test_upload_parse_and_preview(client, tmp_path):
    pdf = make_text_pdf(tmp_path / "rag-intro.pdf", SAMPLE_LINES)

    payload = _upload(client, pdf)
    assert payload["items"], payload
    item = payload["items"][0]
    assert item["status_label"] in {"待解析", "解析中", "解析完成"}
    assert item["source"] == "direct"  # 测试环境关闭了 Kafka

    final = _wait_terminal(client, item["id"])
    assert final["status"] == "done", final["error"]
    assert final["page_count"] == 1
    assert final["chunk_count"] >= 1
    assert final["parser"] == "lite"
    assert final["status_label"] == "解析完成"
    assert final["progress"] == 1.0

    preview = client.get(f"/api/documents/{item['id']}/preview")
    assert preview.status_code == 200
    assert "retrieval" in preview.json()["markdown"].lower()

    markdown = client.get(f"/api/documents/{item['id']}/markdown")
    assert markdown.status_code == 200
    assert "Kafka" in markdown.text

    listing = client.get("/api/documents").json()
    assert listing["counts"]["done"] >= 1
    assert any(doc["id"] == item["id"] for doc in listing["items"])


def test_text_only_chat_returns_sources(client, tmp_path):
    pdf = make_text_pdf(tmp_path / "chat-source.pdf", SAMPLE_LINES)
    doc = _upload(client, pdf)["items"][0]
    final = _wait_terminal(client, doc["id"])
    assert final["status"] == "done", final["error"]

    response = client.post(
        "/api/chat",
        json={
            "question": "What does the document say about Kafka and parsing?",
            "doc_ids": [final["id"]],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["mock"] is True
    assert payload["message"]["sources"], payload
    assert payload["message"]["content_html"]
    assert payload["retrieval"]["text_hits"] >= 1
    assert payload["session_id"]

    history = client.get(f"/api/chat/{payload['session_id']}")
    assert history.status_code == 200
    assert len(history.json()["messages"]) == 2


def test_chat_with_image(client, tmp_path):
    pdf = make_text_pdf(tmp_path / "chat-image.pdf", SAMPLE_LINES)
    doc = _upload(client, pdf)["items"][0]
    assert _wait_terminal(client, doc["id"])["status"] == "done"

    png = make_png(tmp_path / "question.png")
    data_url = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode("ascii")

    response = client.post(
        "/api/chat",
        json={
            "question": "这张图和文档里的流程有什么关系？",
            "doc_ids": [doc["id"]],
            "image": data_url,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["user_message"]["images"], payload
    assert payload["user_message"]["images"][0]["url"].startswith("data:image/jpeg;base64,")


def test_chat_requires_question_or_image(client):
    response = client.post("/api/chat", json={"question": "", "image": None})
    assert response.status_code == 400


def test_upload_rejects_non_pdf(client, tmp_path):
    txt = tmp_path / "note.txt"
    txt.write_text("not a pdf", encoding="utf-8")
    with txt.open("rb") as handle:
        response = client.post(
            "/api/documents", files=[("files", ("note.txt", handle, "text/plain"))]
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["warnings"]


def test_health_and_config(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    data = health.json()
    assert data["ok"] is True
    assert data["kafka"]["enabled"] is False
    assert data["parser"]["active"] in {"lite", "mineru", "mineru-unavailable"}

    config = client.get("/api/config")
    assert config.status_code == 200
    assert "limits" in config.json()


def test_delete_document(client, tmp_path):
    pdf = make_text_pdf(tmp_path / "to-delete.pdf", ["Delete me."])
    doc = _upload(client, pdf)["items"][0]
    assert _wait_terminal(client, doc["id"])["status"] == "done"

    deleted = client.delete(f"/api/documents/{doc['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
