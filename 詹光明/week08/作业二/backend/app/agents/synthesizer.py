"""综合 agent：笔记 + 来源 → 报告骨架与分节正文。

**用 per-section 模式而不是一次性生成整篇。** 一次吐几千字很容易撞上
``max_tokens`` 被截断，截断出来的 JSON 是残缺的，修复链也救不回来；拆成
「先出标题+摘要+结论，再逐个子问题出正文」之后，每次输出都短，失败也只影响一节。
代价是调用数从 1 变成 1+N。

**引用一律走后处理。** 这里产出的正文还要过一遍 ``citation.process_text``：
LLM 写的编号会被逐一核对，对不上的替换掉，没引用的句子强制标成模型推断。
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from ..citation import find_markers, process_text
from ..llm import LLMClient, LLMError
from ..models import (
    Conclusion,
    Note,
    Report,
    ReportSection,
    ResearchState,
    Source,
)
from .prompts import (
    CITATION_RULES,
    SYNTHESIZER_HEAD_PROMPT,
    SYNTHESIZER_SECTION_PROMPT,
    render_notes,
    render_sources,
)

logger = logging.getLogger(__name__)

# 一节正文最多塞多少条来源 —— 只是为了防止 prompt 过长
MAX_SECTION_SOURCES = 12


class _HeadConclusion(BaseModel):
    text: str = ""
    sids: list[str] = Field(default_factory=list)


class SynthesisHead(BaseModel):
    title: str = ""
    executive_summary: str = ""
    key_conclusions: list[_HeadConclusion] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class SectionOutput(BaseModel):
    heading: str = ""
    body_md: str = ""


def _fallback_head(topic: str, notes: list[Note]) -> SynthesisHead:
    """综合失败时的骨架 —— 报告至少要有形状，内容可以空。"""
    return SynthesisHead(
        title=topic,
        executive_summary="（本次未能生成摘要 —— 综合阶段的模型调用失败。）",
        key_conclusions=[],
        open_questions=["模型调用失败，未能生成结论；请重试或更换模型。"],
    )


def _sources_for(notes: list[Note], state: ResearchState) -> list[Source]:
    """取这些笔记引用到的来源，去重后保序。"""
    seen: set[str] = set()
    sources: list[Source] = []
    for note in notes:
        if note.sid in seen:
            continue
        source = state.source_by_sid(note.sid)
        if source is not None:
            seen.add(note.sid)
            sources.append(source)
    return sources[:MAX_SECTION_SOURCES]


def _normalize_conclusions(
    items: list[_HeadConclusion], valid_sids: set[str]
) -> tuple[list[Conclusion], int]:
    """过滤掉编造的编号，返回 (结论列表, 无来源结论数)。

    **编号对不上就丢掉那个编号本身，而不是整条结论** —— 结论的内容可能是有价值的，
    只是 LLM 记错了编号；丢掉编号后它会自动落到「模型推断」那一类，如实公示。
    """
    conclusions: list[Conclusion] = []
    unsourced = 0

    for item in items:
        text = item.text.strip()
        if not text:
            continue
        sids = [sid for sid in item.sids if sid in valid_sids]
        if not sids:
            unsourced += 1
        conclusions.append(
            Conclusion(text=text, sids=sids, basis="sourced" if sids else "inferred")
        )

    return conclusions, unsourced


async def synthesize(
    state: ResearchState,
    llm: LLMClient,
) -> Report:
    """产出报告。**不抛异常** —— 任何一节失败都降级，报告照出。"""
    valid_sids = state.valid_sids
    readable = state.readable_sources
    notes = [n for n in state.notes if n.sid in valid_sids]
    topic = state.request.topic

    as_of = state.info_cutoff
    as_of_text = as_of.strftime("%Y-%m-%d") if as_of else "来源未提供发布时间，无法确定"

    # ── 骨架：标题 / 摘要 / 关键结论 / 遗留问题 ──────────────
    head_prompt = SYNTHESIZER_HEAD_PROMPT.substitute(
        topic=topic,
        as_of=as_of_text,
        sources=render_sources(readable),
        notes=render_notes(notes),
        citation_rules=CITATION_RULES,
    )

    try:
        head = await llm.complete_json(head_prompt, SynthesisHead)
    except LLMError as exc:
        logger.warning("综合骨架生成失败: %s", exc)
        head = _fallback_head(topic, notes)

    conclusions, unsourced_count = _normalize_conclusions(head.key_conclusions, valid_sids)

    # ── 分节正文：每个子问题一次调用 ─────────────────────────
    sections = await _build_sections(state, notes, llm, valid_sids)

    return Report(
        topic=topic,
        title=head.title.strip() or topic,
        executive_summary=head.executive_summary.strip(),
        sections=sections,
        key_conclusions=conclusions,
        open_questions=[q.strip() for q in head.open_questions if q.strip()],
        as_of=as_of,
        citation_stats={"unsourced_conclusions": unsourced_count},
    )


async def _build_sections(
    state: ResearchState,
    notes: list[Note],
    llm: LLMClient,
    valid_sids: set[str],
) -> list[ReportSection]:
    """并发跑所有子问题分节，每节独立降级。"""
    sub_questions = state.sub_questions
    if not sub_questions:
        return []

    async def _one(qid: str, question: str) -> ReportSection:
        section_notes = [n for n in notes if n.qid == qid]
        section_sources = _sources_for(section_notes, state)

        if not section_notes:
            # 这个子问题一条材料都没有 —— 不浪费一次 LLM 调用，如实说明。
            # 但要分清是哪一种「没有」：来源一条没搜到，和搜到了却没能读出来
            # 是两回事，说反了会让人以为该主题没资料。
            if state.readable_sources:
                body = (
                    "现有材料未能回答该子问题：已获取的来源里没能提取出可用材料"
                    "（阅读阶段的模型调用未成功），请直接查阅下方来源列表。"
                )
            else:
                body = "现有材料未能回答该子问题：本次检索没有获取到与之相关的可用来源。"
            return ReportSection(heading=question, body_md=body, qid=qid)

        prompt = SYNTHESIZER_SECTION_PROMPT.substitute(
            topic=state.request.topic,
            question=question,
            sources=render_sources(section_sources),
            notes=render_notes(section_notes),
            citation_rules=CITATION_RULES,
        )

        try:
            output = await llm.complete_json(prompt, SectionOutput)
        except LLMError as exc:
            logger.warning("分节生成失败 %s: %s", qid, exc)
            return ReportSection(
                heading=question,
                body_md="（本节正文生成失败 —— 模型调用出错，请参考下方来源自行查阅。）",
                qid=qid,
            )

        body, stats = process_text(output.body_md, valid_sids)
        return ReportSection(
            heading=output.heading.strip() or question,
            body_md=body,
            qid=qid,
            citation_sids=sorted({sid for sid in find_markers(body) if sid in valid_sids}),
        )

    return list(await asyncio.gather(*(_one(q.qid, q.text) for q in sub_questions)))


__all__ = ["SectionOutput", "SynthesisHead", "synthesize"]
