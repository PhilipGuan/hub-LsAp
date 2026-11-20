"""Hybrid-ish retrieval over MinerU text, tables, and figure captions."""

from __future__ import annotations

import logging
from typing import Any

from .config import (
    RETRIEVAL_CANDIDATES,
    RETRIEVAL_IMAGE_TOP_K,
    RETRIEVAL_TEXT_TOP_K,
)
from .db import (
    STATUS_COMPLETED,
    chunks_by_document,
    fallback_chunks,
    fts_search,
    images_for_documents,
    list_documents,
)
from .textutils import is_visual_query, search_expression, snippet, tokenize

logger = logging.getLogger(__name__)

KIND_LABELS = {"text": "正文", "table": "表格", "image": "插图"}


def _normalise_row(row: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    score = max(0.0, -float(row.get("rank") or 0.0))
    image_path = row.get("image_path")
    return {
        "chunk_id": row["id"],
        "doc_id": row["doc_id"],
        "doc_name": document.get("title") or document.get("filename") or "未知文档",
        "filename": document.get("filename"),
        "page": int(row.get("page") or 1),
        "kind": row.get("kind") or "text",
        "kind_label": KIND_LABELS.get(row.get("kind") or "text", "资料"),
        "heading": row.get("heading") or "",
        "content": row.get("content") or "",
        "caption": row.get("caption") or "",
        "snippet": snippet(row.get("content") or ""),
        "image_path": image_path,
        "image_url": f"/media/{image_path}" if image_path else None,
        "score": round(score, 5),
    }


def _dedupe(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_content: set[str] = set()
    seen_images: set[str] = set()
    for ref in references:
        content_key = " ".join(ref["content"].split())[:240]
        image_key = ref.get("image_path")
        if content_key and content_key in seen_content:
            continue
        if image_key and image_key in seen_images:
            continue
        if content_key:
            seen_content.add(content_key)
        if image_key:
            seen_images.add(image_key)
        result.append(ref)
    return result


def _overview_rows(
    doc_ids: list[str] | None, *, per_document: int = 2
) -> list[dict[str, Any]]:
    documents = [
        doc
        for doc in list_documents(300)
        if doc["status"] == STATUS_COMPLETED and (not doc_ids or doc["id"] in doc_ids)
    ][:8]
    rows: list[dict[str, Any]] = []
    for document in documents:
        chunks = chunks_by_document(document["id"])
        chosen = 0
        for chunk in chunks:
            if chosen >= per_document:
                break
            if chunk["kind"] in {"text", "table"} and len(chunk["content"]) > 30:
                chunk = dict(chunk)
                chunk["rank"] = 0.0
                rows.append(chunk)
                chosen += 1
    return rows


def retrieve(
    query: str,
    doc_ids: list[str] | None = None,
    *,
    query_tokens: list[str] | None = None,
) -> dict[str, Any]:
    """Retrieve citable text/table units plus figure chunks.

    FTS5 BM25 is the primary ranker. The query image description (when present)
    is folded into the same query before this function is called.
    """
    tokens = query_tokens if query_tokens is not None else tokenize(query)
    expression = search_expression(tokens)
    visual = is_visual_query(query)
    rows: list[dict[str, Any]] = []
    if expression:
        try:
            rows = fts_search(expression, doc_ids, limit=RETRIEVAL_CANDIDATES)
        except Exception as exc:
            logger.warning("FTS search failed: %s", exc)

    document_map = {doc["id"]: doc for doc in list_documents(500)}
    text_rows = [row for row in rows if row.get("kind") != "image"]
    image_rows = [row for row in rows if row.get("kind") == "image"]

    # A question such as “总结一下这份文档” has no useful search terms.
    if not text_rows:
        text_rows = _overview_rows(doc_ids)
    if not image_rows and visual:
        image_rows = images_for_documents(doc_ids, limit=RETRIEVAL_IMAGE_TOP_K * 3)
    if not image_rows and not rows:
        image_rows = images_for_documents(doc_ids, limit=RETRIEVAL_IMAGE_TOP_K)

    text_refs = [
        _normalise_row(row, document_map.get(row["doc_id"], {}))
        for row in text_rows[:RETRIEVAL_TEXT_TOP_K]
    ]
    image_refs = [
        _normalise_row(row, document_map.get(row["doc_id"], {}))
        for row in image_rows[:RETRIEVAL_IMAGE_TOP_K]
    ]

    # Ensure a scoped knowledge base always contributes a little context, even
    # when a short query misses every FTS term.
    if not text_refs and not image_refs:
        rows = fallback_chunks(doc_ids, limit=6)
        text_refs = [
            _normalise_row(row, document_map.get(row["doc_id"], {})) for row in rows
        ]

    references = _dedupe(text_refs + image_refs)
    for index, ref in enumerate(references, start=1):
        ref["n"] = index

    return {
        "references": references,
        "tokens": tokens,
        "visual_query": visual,
        "query": query,
    }
