"""RAG 对外服务：把检索、回答、索引维护包成简单函数。"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .answer import answer_question, render_markdown
from .index import Hit, index
from .retriever import Retrieval, retrieve

__all__ = [
    "Hit",
    "Retrieval",
    "answer_question",
    "index",
    "refresh_index",
    "render_markdown",
    "retrieve",
    "stats",
]


def refresh_index(force: bool = False) -> None:
    index.ensure_fresh(force=force)


def stats() -> dict[str, Any]:
    data = index.stats()
    data["retrieval_mode"] = "hybrid" if index.dense_enabled else "bm25"
    return data


def search_debug(question: str, doc_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """给调试接口用：返回召回明细。"""
    retrieval = retrieve(question, doc_ids=doc_ids)
    return {
        "retrieval": retrieval.summary(),
        "text": [hit.as_source(i + 1) for i, hit in enumerate(retrieval.text_hits)],
        "figures": [hit.as_source(i + 1) for i, hit in enumerate(retrieval.figure_hits)],
    }
