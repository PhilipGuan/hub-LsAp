"""功能3 接口测试：图问入参、检索召回、多模态 Prompt 组装、SSE 返回。

说明：测试环境 LLM_BACKEND=mock（chat_service 内置桩回答）、EMBEDDING 关闭走
关键词降级检索，故本文件不依赖网络与向量索引；真实 LLM / 向量链路由联调覆盖。
"""
import base64

import pytest

from app.db.models import Chunk, ChunkType, Conversation, Document, DocumentStatus, Message
from app.services.chat_service import _build_multimodal_messages
from app.services.retrieval_service import ContextItem

from tests.conftest import parse_sse

# 1x1 PNG 魔数开头的最小图片
TINY_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
).decode()


def _session_factory():
    from app.db.base import SessionLocal

    return SessionLocal()


def _seed_doc(db, doc_id: str, file_name: str, chunks: list[dict]) -> None:
    """chunks: [{seq, type, content, image_url?, page_no?}]"""
    db.add(Document(id=doc_id, file_name=file_name, file_hash=f"hash-{doc_id}",
                    storage_path=f"uploads/{doc_id}.pdf", size=1, status=DocumentStatus.SUCCESS))
    for c in chunks:
        db.add(Chunk(id=f"chunk-{doc_id}-{c['seq']}", document_id=doc_id, seq=c["seq"],
                     chunk_type=ChunkType(c["type"]), content=c["content"],
                     image_url=c.get("image_url"), page_no=c.get("page_no", 1)))
    db.commit()


def _chat(client, payload: dict) -> list[dict]:
    resp = client.post("/api/v1/chat", json=payload)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    return parse_sse(resp.text)


# ---------- F3-3 / F3-8：检索召回 + SSE 事件序列 ----------
def test_chat_sse_flow(client):
    with _session_factory() as db:
        _seed_doc(db, "doc-a", "测试文档A.pdf", [
            {"seq": 0, "type": "TEXT",
             "content": "本系统基于 Kafka 解耦文档上传与解析流程，MinerU 负责 PDF 解析并产出 Markdown。"},
        ])

    events = _chat(client, {"query": "Kafka 解耦", "top_k": 5})
    kinds = [e["type"] for e in events]
    assert kinds[0] == "meta"
    assert "status" in kinds
    assert kinds[-1] == "done"
    assert "error" not in kinds

    answer = "".join(e["content"] for e in events if e["type"] == "chunk")
    assert "Mock 模式" in answer  # mock 桩回答包含检索结果
    assert "Kafka" in answer

    done = events[-1]
    assert len(done["citations"]) == 1
    assert done["citations"][0]["file_name"] == "测试文档A.pdf"
    assert done["citations"][0]["page_no"] == 1

    # F3-7：会话与消息持久化（user + assistant 各一条）
    with _session_factory() as db:
        conv_id = events[0]["conversation_id"]
        msgs = db.query(Message).filter(Message.conversation_id == conv_id)\
            .order_by(Message.created_at.asc()).all()
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[0].text_content == "Kafka 解耦"
        assert "Kafka" in msgs[1].text_content


# ---------- F3-2：按文档过滤检索范围 ----------
def test_chat_document_scope_filter(client):
    with _session_factory() as db:
        _seed_doc(db, "doc-a", "A.pdf", [
            {"seq": 0, "type": "TEXT", "content": "红色圆形示意图出现在第二页。"},
        ])
        _seed_doc(db, "doc-b", "B.pdf", [
            {"seq": 0, "type": "TEXT", "content": "红色圆形示意图出现在第三页。"},
        ])

    events = _chat(client, {"query": "红色圆形示意图", "document_ids": ["doc-b"]})
    assert events[-1]["type"] == "done"
    cits = events[-1]["citations"]
    assert cits, "应至少召回一条"
    assert {c["document_id"] for c in cits} == {"doc-b"}


# ---------- F3-1：图问入参校验 ----------
def test_chat_rejects_invalid_image(client):
    bad = base64.b64encode(b"plain text not an image").decode()
    resp = client.post("/api/v1/chat", json={"query": "问题", "images": [bad]})
    assert resp.status_code == 400
    assert resp.json()["code"] == 1001
    assert "图片格式不支持" in resp.json()["message"]


def test_chat_rejects_over_limit_image_count(client):
    images = [TINY_PNG] * 4  # MAX_CHAT_IMAGES = 3
    resp = client.post("/api/v1/chat", json={"query": "问题", "images": images})
    assert resp.status_code == 400
    assert resp.json()["code"] == 1001


def test_chat_accepts_png_image(client):
    with _session_factory() as db:
        _seed_doc(db, "doc-img", "含图.pdf", [
            {"seq": 0, "type": "TEXT", "content": "这一页包含一张示意图。"},
        ])
    events = _chat(client, {"query": "图片里是什么", "images": [TINY_PNG]})
    assert events[-1]["type"] == "done"

    conv_id = events[0]["conversation_id"]
    with _session_factory() as db:
        user_msg = db.query(Message).filter(
            Message.conversation_id == conv_id, Message.role == "user").one()
        import json

        # ORM 列为 JSON 字符串，解析后应为 URL 数组
        urls = json.loads(user_msg.image_urls) if user_msg.image_urls else []
        assert isinstance(urls, list)


# ---------- F3-4：多模态 Prompt 组装（资料图片 + 用户图片均入消息） ----------
def test_multimodal_prompt_assembly(client, storage):
    storage.save("images/doc-a/000.png", b"fake-image-bytes")
    items = [
        ContextItem(chunk_id="c1", chunk_type="TEXT", content="资料正文",
                    document_id="doc-a", file_name="含图.pdf", page_no=2),
        ContextItem(chunk_id="c2", chunk_type="IMAGE", content="图片上下文：示意图",
                    document_id="doc-a", file_name="含图.pdf", page_no=2,
                    image_url="/static/images/doc-a/000.png", image_key="images/doc-a/000.png"),
    ]
    user_images = [{"storage_url": "/static/chat/x.png",
                    "data_url": "data:image/png;base64,QUJD"}]

    with _session_factory() as db:
        db.add(Conversation(id="conv-prompt", title="t"))
        db.commit()
        messages = _build_multimodal_messages("这是什么图", user_images, items, storage, db, "conv-prompt")

    assert messages[0]["role"] == "system"
    user_msg = messages[-1]
    assert user_msg["role"] == "user"
    text_part = user_msg["content"][0]
    assert text_part["type"] == "text"
    assert "【参考资料】" in text_part["text"]
    assert "[资料1]" in text_part["text"]
    assert "[图片1]" in text_part["text"]
    assert "【用户问题】这是什么图" in text_part["text"]
    assert "用户还上传了图片" in text_part["text"]

    image_parts = [p for p in user_msg["content"] if p["type"] == "image_url"]
    assert len(image_parts) == 2  # 1 张召回资料图片 + 1 张用户图片
    urls = [p["image_url"]["url"] for p in image_parts]
    assert any(u.startswith("data:image/png;base64,") for u in urls)  # 资料图片转 data URL


# ---------- F3-7：多轮对话上下文持久化 ----------
def test_multi_turn_conversation(client):
    with _session_factory() as db:
        _seed_doc(db, "doc-c", "C.pdf", [
            {"seq": 0, "type": "TEXT", "content": "系统采用向量库存储语义索引。"},
        ])
    first = _chat(client, {"query": "向量库"})
    conv_id = first[0]["conversation_id"]
    second = _chat(client, {"query": "向量库 再答一次", "conversation_id": conv_id})
    assert second[0]["conversation_id"] == conv_id

    with _session_factory() as db:
        msgs = db.query(Message).filter(Message.conversation_id == conv_id).all()
        assert len(msgs) == 4  # 两轮 user+assistant


# ---------- 边界：不存在的会话 / 未命中检索 ----------
def test_chat_unknown_conversation_yields_error_event(client):
    events = _chat(client, {"query": "随便问", "conversation_id": "no-such-conv"})
    assert events[-1]["type"] == "error"
    assert "会话不存在" in events[-1]["message"]


def test_chat_no_hits_still_answers(client):
    events = _chat(client, {"query": "完全无关的词组xyz"})
    assert events[-1]["type"] == "done"
    assert events[-1]["citations"] == []
    answer = "".join(e["content"] for e in events if e["type"] == "chunk")
    assert answer  # mock 桩在空召回时仍给出可预期的回答
