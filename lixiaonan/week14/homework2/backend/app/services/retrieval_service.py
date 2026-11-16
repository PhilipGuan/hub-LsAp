"""检索服务（功能3 / F3-3）。

- 向量检索（EMBEDDING_ENABLED=true）：问题向量化 -> FAISS 召回 Top-K 文本块与图片（图片以
  「周边上下文文本 / 可选的 LLM 图片描述」作为向量文本）
- 关键词降级（未开启向量化或索引为空）：按词频打分，仅文本块
"""
import re
from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Chunk, ChunkType, Document


@dataclass
class ContextItem:
    """一个召回片段，供问答 Prompt 组装与来源标注。"""
    chunk_id: str
    chunk_type: str           # TEXT / IMAGE
    content: str
    document_id: str
    file_name: str
    page_no: int | None = None
    image_url: str | None = None      # 图片可访问 URL（回答中引用用）
    image_key: str | None = None      # 图片存储 key（读字节转 data URL 用）
    score: float | None = None


def retrieve(db: Session, query: str, settings: Settings,
             document_ids: list[str] | None = None, top_k: int = 5) -> list[ContextItem]:
    if settings.embedding_enabled:
        try:
            return _vector_retrieve(db, query, settings, document_ids, top_k)
        except ImportError as exc:
            logger.warning(f"向量化依赖不可用，降级关键词检索: {exc}")
    return _keyword_retrieve(db, query, document_ids, top_k)


def _load_doc_map(db: Session, doc_ids: set[str]) -> dict[str, Document]:
    if not doc_ids:
        return {}
    rows = db.query(Document).filter(Document.id.in_(doc_ids)).all()
    return {d.id: d for d in rows}


def _vector_retrieve(db: Session, query: str, settings: Settings,
                     document_ids: list[str] | None, top_k: int) -> list[ContextItem]:
    from worker.indexer.embedder import embed_texts
    from worker.indexer.vector_store import FaissVectorStore

    store = FaissVectorStore(settings)
    if store.count() == 0:
        logger.warning("向量索引为空，本次降级为关键词检索")
        return _keyword_retrieve(db, query, document_ids, top_k)

    query_vector = embed_texts([query], settings, is_query=True)[0]
    hits = store.search(query_vector, top_k, filter_document_ids=document_ids or None)

    chunk_ids = [h["metadata"]["chunk_id"] for h in hits]
    rows = db.query(Chunk).filter(Chunk.id.in_(chunk_ids)).all()
    row_map = {r.id: r for r in rows}
    doc_map = _load_doc_map(db, {r.document_id for r in rows})

    items: list[ContextItem] = []
    for hit in hits:
        row = row_map.get(hit["metadata"]["chunk_id"])
        if row is None:
            continue  # 元数据过期（文档被重解析）
        doc = doc_map.get(row.document_id)
        items.append(ContextItem(
            chunk_id=row.id,
            chunk_type=row.chunk_type.value,
            content=row.content,
            document_id=row.document_id,
            file_name=doc.file_name if doc else "?",
            page_no=row.page_no,
            image_url=row.image_url,
            image_key=hit["metadata"].get("image_key"),
            score=hit["score"],
        ))
    return items


def _keyword_retrieve(db: Session, query: str,
                      document_ids: list[str] | None, top_k: int) -> list[ContextItem]:
    terms = [t for t in re.split(r"[\s，。,.?!？!；;：:]+", query) if len(t) >= 2] or [query]
    q = (
        db.query(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .filter(Chunk.chunk_type == ChunkType.TEXT)
    )
    if document_ids:
        q = q.filter(Chunk.document_id.in_(document_ids))
    scored = []
    for chunk, doc in q.all():
        score = sum(chunk.content.count(t) for t in terms)
        if score > 0:
            scored.append((score, chunk, doc))
    scored.sort(key=lambda x: -x[0])
    return [
        ContextItem(
            chunk_id=chunk.id, chunk_type=chunk.chunk_type.value, content=chunk.content,
            document_id=chunk.document_id, file_name=doc.file_name,
            page_no=chunk.page_no, image_url=chunk.image_url, score=float(score),
        )
        for score, chunk, doc in scored[:top_k]
    ]
