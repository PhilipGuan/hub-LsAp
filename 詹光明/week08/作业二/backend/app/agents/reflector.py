"""反思 agent：判断材料够不够、缺什么、下一轮搜什么。

**这里只产出 LLM 的看法，不做终止决定。** 决定在 ``stopping.py`` —— LLM 有过早
收手的已知偏差（搜了两轮就说「已经足够」），所以它的 ``sufficient`` 只是终止判定的
**一个**输入，还必须同时满足程序算出来的覆盖率与无来源占比门槛。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..llm import LLMClient, LLMError
from ..models import Note, SubQuestion
from .prompts import REFLECTOR_PROMPT, render_notes

logger = logging.getLogger(__name__)


class _Gap(BaseModel):
    qid: str = ""
    gap: str = ""


class _NextQuery(BaseModel):
    qid: str = ""
    query: str = ""
    reason: str = ""


class ReflectorOutput(BaseModel):
    sufficient: bool = False
    coverage: dict[str, float] = Field(default_factory=dict)
    gaps: list[_Gap] = Field(default_factory=list)
    next_queries: list[_NextQuery] = Field(default_factory=list)
    rationale: str = ""


class ReflectionOutcome(BaseModel):
    sufficient: bool = False
    coverage: dict[str, float] = Field(default_factory=dict)
    gaps: list[dict[str, str]] = Field(default_factory=list)
    next_queries: list[dict[str, str]] = Field(default_factory=list)
    rationale: str = ""
    degraded: bool = False


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _fallback_outcome(reason: str) -> ReflectionOutcome:
    """LLM 挂了就说「够了」收尾 —— 材料已经拿到手，硬撑着再搜只会烧钱。

    注意这不等于立刻终止：``stopping.py`` 还要求覆盖率达标，
    覆盖率不够时会自己改判成继续（或按停滞/轮次上限收尾）。
    """
    return ReflectionOutcome(
        sufficient=True,
        rationale=f"反思失败，按「材料已足够」收尾：{reason}",
        degraded=True,
    )


def _render_coverage(sub_questions: list[SubQuestion], notes: list[Note]) -> str:
    if not sub_questions:
        return "（无子问题）"
    lines: list[str] = []
    for sub in sub_questions:
        count = len([n for n in notes if n.qid == sub.qid])
        lines.append(f"- [{sub.qid}] {sub.text}  （当前 {count} 条笔记）")
    return "\n".join(lines)


async def reflect(
    topic: str,
    sub_questions: list[SubQuestion],
    notes: list[Note],
    used_queries: list[str],
    llm: LLMClient,
    *,
    round_no: int,
    max_rounds: int,
) -> ReflectionOutcome:
    """审查当前进度。**不抛异常**。"""
    prompt = REFLECTOR_PROMPT.substitute(
        topic=topic,
        round=round_no,
        max_rounds=max_rounds,
        coverage=_render_coverage(sub_questions, notes),
        notes=render_notes(notes),
        used_queries="\n".join(f"- {q}" for q in used_queries) or "（还没有搜过）",
    )

    try:
        output = await llm.complete_json(prompt, ReflectorOutput)
    except LLMError as exc:
        logger.warning("反思失败: %s", exc)
        return _fallback_outcome(str(exc))

    valid_qids = {sub.qid for sub in sub_questions}

    coverage = {
        qid: _clamp(score)
        for qid, score in output.coverage.items()
        # 丢掉 LLM 编出来的 qid，它会让覆盖率统计莫名其妙地多出几项
        if qid in valid_qids and isinstance(score, (int, float))
    }

    gaps = [
        {"qid": g.qid if g.qid in valid_qids else "", "gap": g.gap.strip()}
        for g in output.gaps
        if g.gap.strip()
    ]

    next_queries = [
        {"qid": q.qid if q.qid in valid_qids else "", "query": q.query.strip(), "reason": q.reason}
        for q in output.next_queries
        if q.query.strip()
    ]

    return ReflectionOutcome(
        sufficient=output.sufficient,
        coverage=coverage,
        gaps=gaps,
        next_queries=next_queries,
        rationale=output.rationale.strip(),
    )


__all__ = ["ReflectionOutcome", "ReflectorOutput", "reflect"]
