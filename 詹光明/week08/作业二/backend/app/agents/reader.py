"""阅读 agent：单个来源的正文 → 若干条 Note。

**sid 不由 LLM 产出，而是程序强制写进去的。** 这是引用可追溯的关键一环 ——
如果让模型自己填编号，它写出来的编号就没法信任了。让 LLM 只负责「抽什么事实」，
「这条事实来自哪一篇」由程序决定，这一层不确定性就直接消掉了。
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from ..llm import LLMClient, LLMError, LLMTruncatedError
from ..models import ContentOrigin, Note, Source
from .prompts import READER_PROMPT

logger = logging.getLogger(__name__)

# 正文低于这个长度就不值得花一次 LLM 调用 —— 也抽不出什么
MIN_READABLE_CHARS = 60


class _ReadNote(BaseModel):
    claim: str = ""
    evidence: str = ""
    quote: str | None = None
    strength: float = 0.5


class ReaderOutput(BaseModel):
    notes: list[_ReadNote] = Field(default_factory=list)
    summary: str = ""


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


async def read_source(
    source: Source,
    sub_questions_text: str,
    llm: LLMClient,
    *,
    round_no: int = 0,
) -> list[Note]:
    """从单个来源抽取笔记。**不抛异常** —— 失败返回空列表，引擎照常往下走。

    返回空列表是诚实的降级：这一次确实没抽出东西，如实记下来，
    比硬凑一条主张污染报告要好。
    """
    content = (source.content or "").strip()
    if len(content) < MIN_READABLE_CHARS:
        return []

    is_weak = source.content_origin is ContentOrigin.snippet

    prompt = READER_PROMPT.substitute(
        sub_questions=sub_questions_text or "（未指定）",
        title=source.title or "（无标题）",
        site_name=source.site_name or source.domain or "未知",
        origin=source.content_origin.value,
        content=content,
    )

    try:
        output = await llm.complete_json(prompt, ReaderOutput)
    except LLMTruncatedError as exc:
        # 预算被思考吃完 ≠ 这页读不了。同一份 prompt 换更大的预算就能过，
        # 直接放弃会白白丢掉一篇完全读得下来的来源（实测 5/30 条）。
        logger.info("阅读被截断，加大预算重试 %s: %s", source.sid, exc)
        try:
            output = await llm.complete_json(prompt, ReaderOutput, max_tokens=llm.retry_max_tokens)
        except LLMError as retry_exc:
            logger.warning("阅读失败，本条来源记 0 条笔记 %s: %s", source.sid, retry_exc)
            return []
    except LLMError as exc:
        logger.warning("阅读失败，本条来源记 0 条笔记 %s: %s", source.sid, exc)
        return []

    notes: list[Note] = []
    for i, item in enumerate(output.notes, 1):
        claim = item.claim.strip()
        if not claim:
            continue
        notes.append(
            Note(
                note_id=f"{source.sid}-N{i}",
                sid=source.sid,  # ← 程序赋值，不信任 LLM
                claim=claim,
                evidence=item.evidence.strip(),
                quote=(item.quote or "").strip() or None,
                strength=_clamp(item.strength),
                weak=is_weak,
                round=round_no,
            )
        )

    return notes


async def read_many(
    sources: list[Source],
    sub_questions_text: str,
    llm: LLMClient,
    *,
    round_no: int = 0,
) -> list[Note]:
    """并发读一批来源。LLM 客户端的信号量会压住实际并发。"""
    batches = await asyncio.gather(
        *(read_source(s, sub_questions_text, llm, round_no=round_no) for s in sources if s.readable)
    )
    return [note for batch in batches for note in batch]


__all__ = ["MIN_READABLE_CHARS", "ReaderOutput", "read_many", "read_source"]
