"""解析 → 分块 → 向量化 → 入 ES 的完整管线。"""

import logging
from pathlib import Path

from ..config import get_settings
from .chunker import split_markdown
from .embedder import embed_texts
from .es_store import delete_document, index_chunks
from .mineru_parser import parse_pdf

log = logging.getLogger(__name__)


def process_document(doc_id: str, file_path: str, filename: str) -> dict:
    settings = get_settings()
    parsed = parse_pdf(Path(file_path), doc_id)
    chunks = split_markdown(parsed.markdown, doc_id, filename)
    if not chunks:
        raise RuntimeError("解析成功但没有提取到文本内容")

    vectors = embed_texts([c.content for c in chunks])
    if len(vectors) != len(chunks):
        raise RuntimeError(f"embedding 数量不匹配: {len(vectors)} != {len(chunks)}")

    delete_document(doc_id)  # 重复解析时先清旧块
    operations: list[dict] = []
    for chunk, vector in zip(chunks, vectors):
        operations.append(
            {"index": {"_index": settings.es_index, "_id": chunk.chunk_id}}
        )
        operations.append(chunk.to_es_doc(vector, settings.es_index)["_source"])
    index_chunks(operations)

    log.info(
        "doc %s indexed: pages=%s chunks=%s images=%s",
        doc_id,
        parsed.page_count,
        len(chunks),
        parsed.image_count,
    )
    return {
        "page_count": parsed.page_count,
        "chunk_count": len(chunks),
        "image_count": parsed.image_count,
    }
