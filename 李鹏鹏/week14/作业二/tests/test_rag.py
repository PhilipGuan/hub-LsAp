"""切块与检索的单元测试（不依赖 HTTP / Kafka）。"""
from __future__ import annotations

from app.config import settings
from app.parsing.base import Block, ParsedDocument
from app.parsing.chunker import build_chunks
from app.rag.answer import _replace_markers
from app.rag.index import Hit, RagIndex, tokenize
from tests.pdf_factory import make_png


def test_tokenize_mixed_language():
    tokens = tokenize("Kafka 异步解析 PDF，支持图文混排！")
    assert "kafka" in tokens
    assert "pdf" in tokens
    assert any("解析" in token for token in tokens)


def test_chunker_builds_text_and_figure_chunks():
    image = make_png(settings.documents_dir / "doc_unit" / "images" / "figure-0001.png")

    parsed = ParsedDocument(
        backend="lite",
        page_count=2,
        blocks=[
            Block(kind="text", page=1, content="系统架构总览", level=1, meta={"size": 18}),
            Block(kind="text", page=1, content="编码器把输入映射到向量空间，解码器负责生成答案。"),
            Block(
                kind="image",
                page=2,
                image_path="images/figure-0001.png",
                caption="图1 系统架构",
                meta={"width": 72, "height": 48},
            ),
            Block(kind="text", page=2, content="上图展示了端到端的检索增强生成流程。"),
        ],
    )

    chunks = build_chunks("doc_unit", "demo.pdf", parsed)
    assert any(chunk["kind"] == "text" for chunk in chunks)

    figure = next(chunk for chunk in chunks if chunk["kind"] == "figure")
    assert figure["page"] == 2
    assert "系统架构" in figure["content"]
    assert "图前正文" in figure["content"] or "系统架构总览" in figure["content"]
    assert figure["image_url"] == "/media/doc_unit/images/figure-0001.png"
    assert image.is_file()


def test_bm25_prefers_figure_for_figure_question(monkeypatch):
    index = RagIndex()
    index.rebuild(
        [
            {
                "id": "c1",
                "doc_id": "d1",
                "kind": "text",
                "ord": 0,
                "page": 1,
                "section": "简介",
                "content": "本文介绍检索增强生成的背景与动机。",
                "caption": "",
                "filename": "demo.pdf",
            },
            {
                "id": "c2",
                "doc_id": "d1",
                "kind": "figure",
                "ord": 1,
                "page": 3,
                "section": "架构",
                "content": "图片说明：图3 系统架构图，包含上传、Kafka、解析与检索四个模块。",
                "caption": "图3 系统架构图",
                "filename": "demo.pdf",
                "image_url": "/media/d1/images/figure-0003.png",
            },
        ]
    )
    # 单测里跳过数据库指纹刷新
    monkeypatch.setattr(index, "ensure_fresh", lambda force=False: None)

    hits = index.search("系统架构图 包含哪些模块", top_k=3, kinds=["figure"])
    assert hits, "应当召回图片块"
    assert hits[0].chunk["kind"] == "figure"
    assert hits[0].score > 0


def test_replace_markers_maps_rank_to_url():
    chunk = {
        "id": "c1",
        "doc_id": "d1",
        "kind": "figure",
        "page": 2,
        "content": "图片说明：图1 流程",
        "caption": "图1 流程",
        "filename": "demo.pdf",
        "image_url": "/media/d1/images/figure-0001.png",
    }
    item = Hit(chunk=chunk)
    from app.rag.answer import ContextItem

    context = ContextItem(
        rank=1,
        hit=item,
        text=chunk["content"],
        image_url=chunk["image_url"],
        image_path="",
        caption=chunk["caption"],
    )

    markdown, used = _replace_markers("流程如图 ![流程](IMG:1) 所示。", [context])
    assert "/media/d1/images/figure-0001.png" in markdown
    assert "IMG:1" not in markdown
    assert used and used[0]["rank"] == 1

    cleaned, none_used = _replace_markers("不存在 ![x](IMG:9)", [context])
    assert "没有配图" in cleaned
    assert none_used == []
