"""检索编排：查询扩展 → 多路召回 → 图片补充。

多模态体现在两个地方：

1. **文字找图**：图片块在入库时就把图注、所在章节、图前后正文写进了可检索文本，
   因此「第三章的架构图讲了什么」能直接召回图片块；
2. **以图找文**：用户随问题上传图片时，先让视觉模型生成一句图片描述，
   与问题拼成检索查询，再去向量/BM25 索引里找相关内容。

可选的 CLIP 向量（``MULTIMODAL_EMBEDDING_MODEL``）还能让文字直接匹配图片语义。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..config import settings
from . import llm
from .index import Hit, index

logger = logging.getLogger(__name__)

#: 问题里出现这些词时，扩大图片召回数量
_FIGURE_HINTS = ("图", "表", "截图", "示意", "架构", "流程", "界面", "曲线", "照片", "图表")


@dataclass(slots=True)
class Retrieval:
    query: str
    text_hits: list[Hit] = field(default_factory=list)
    figure_hits: list[Hit] = field(default_factory=list)
    caption: str = ""
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def empty(self) -> bool:
        return not self.text_hits and not self.figure_hits

    def summary(self) -> dict[str, object]:
        return {
            "query": self.query,
            "caption": self.caption,
            "text_hits": len(self.text_hits),
            "figure_hits": len(self.figure_hits),
            "elapsed_ms": self.elapsed_ms,
            "warnings": list(self.warnings),
            "top_scores": [round(hit.score, 4) for hit in (self.text_hits + self.figure_hits)[:5]],
        }


def _figure_top_k(question: str) -> int:
    base = max(1, settings.rag_figure_top_k)
    if any(hint in question for hint in _FIGURE_HINTS):
        return base + 2
    return base


def retrieve(
    question: str,
    *,
    doc_ids: Sequence[str] | None = None,
    question_image: str | None = None,
) -> Retrieval:
    """执行检索；``question_image`` 是 data URL（用户随问题上传的图片）。"""
    started = time.perf_counter()
    question = (question or "").strip()
    result = Retrieval(query=question)

    query = question
    if question_image and settings.rag_caption_query:
        caption = llm.describe_image(question_image, hint=question)
        if caption:
            result.caption = caption
            query = f"{question} {caption}".strip()
            result.warnings.append(f"已用视觉模型生成图片描述辅助检索：{caption[:80]}")
    result.query = query

    index.ensure_fresh()
    try:
        result.text_hits = index.search(
            query, doc_ids=doc_ids, top_k=max(1, settings.rag_top_k), kinds=["text", "table"]
        )
        result.figure_hits = index.search(
            query,
            doc_ids=doc_ids,
            top_k=_figure_top_k(question),
            kinds=["figure"],
        )
    except Exception as exc:  # noqa: BLE001 - 检索失败要给用户可读信息
        logger.exception("检索失败")
        result.warnings.append(f"检索出错：{exc}")
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result

    if result.empty:
        result.warnings.append(
            "没有检索到相关内容。请确认文档已解析完成，或换一种问法（例如点出章节名、图表名）。"
        )
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result
