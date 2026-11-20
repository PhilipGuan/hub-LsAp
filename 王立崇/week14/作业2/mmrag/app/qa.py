"""Multimodal RAG question-answering orchestration."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from .config import (
    LLM_MODEL,
    QUERY_IMAGE_AUGMENT,
    QUERY_IMAGE_TIMEOUT_SECONDS,
)
from .llm import answer_question, describe_query_image
from .retrieval import retrieve
from .textutils import tokenize

logger = logging.getLogger(__name__)

_MEDIA_RE = re.compile(r"/media/[^\s)\]\"']+")


def _ensure_visual_layout(answer: str, references: list[dict[str, Any]]) -> str:
    """Guarantee a readable text+image layout even with a small local model."""
    visual_refs = [ref for ref in references if ref.get("image_url")]
    if not visual_refs:
        return answer
    already_embedded = set(_MEDIA_RE.findall(answer))
    missing = [ref for ref in visual_refs if ref["image_url"] not in already_embedded]
    if not missing:
        return answer
    # Avoid duplicating a figure the model already inserted.
    pieces = [answer.rstrip(), "", "**相关图示**", ""]
    for ref in missing[:2]:
        label = ref.get("caption") or ref.get("heading") or "文档插图"
        label = " ".join(label.split())[:70]
        pieces.append(f"![{label}]({ref['image_url']})")
        pieces.append("")
        pieces.append(
            f"*图源：{ref['doc_name']} · 第 {ref['page']} 页 · 资料 [{ref['n']}]*"
        )
        pieces.append("")
    return "\n".join(pieces).strip()


async def answer(
    *,
    question: str,
    image_data_url: str | None,
    doc_ids: list[str] | None,
    history: list[dict[str, str]] | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    query_image_description = ""
    if image_data_url and QUERY_IMAGE_AUGMENT:
        try:
            query_image_description = await asyncio.wait_for(
                describe_query_image(image_data_url, question),
                timeout=QUERY_IMAGE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("image retrieval augmentation failed: %s", exc)

    retrieval_query = question
    if query_image_description:
        retrieval_query = f"{question}\n{query_image_description}"
    tokens = tokenize(retrieval_query)
    result = retrieve(retrieval_query, doc_ids, query_tokens=tokens)
    references = result["references"]

    if not references and not image_data_url:
        return {
            "answer": (
                "当前知识库中没有检索到可用的文档内容。\n\n"
                "请先在左侧上传 PDF，等状态变为 **解析完成** 后再提问；"
                "也可以换一种问法，或勾选指定文档范围。"
            ),
            "references": [],
            "query_tokens": tokens,
            "query_image_description": query_image_description,
            "visual_query": result["visual_query"],
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "model": LLM_MODEL,
        }

    text_answer = await answer_question(
        question=question,
        references=references,
        history=history,
        user_image_data_url=image_data_url,
        query_image_description=query_image_description,
    )
    text_answer = _ensure_visual_layout(text_answer, references)

    return {
        "answer": text_answer,
        "references": references,
        "query_tokens": tokens,
        "query_image_description": query_image_description,
        "visual_query": result["visual_query"],
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "model": LLM_MODEL,
    }
