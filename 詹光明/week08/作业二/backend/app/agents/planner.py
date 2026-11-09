"""规划 agent：研究主题 → 子问题 + 首轮检索词。

LLM 挂掉时退到一组通用子问题模板 —— 这是本项目唯一一处「靠模板硬撑」的
地方，但它撑住的是**起点**：只要起点还在，后面的多轮检索与反思就能照常跑。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..llm import LLMClient, LLMError
from ..models import SubQuestion
from .prompts import PLANNER_PROMPT

logger = logging.getLogger(__name__)

# 兜底模板的四个通用侧面，够覆盖绝大多数主题
_FALLBACK_ANGLES = ("现状与基本事实", "主要参与方与对比", "趋势与预测", "风险与争议")


class _PlannedQuestion(BaseModel):
    text: str = ""
    rationale: str = ""
    queries: list[str] = Field(default_factory=list)


class PlannerOutput(BaseModel):
    sub_questions: list[_PlannedQuestion] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    rationale: str = ""


class PlanResult(BaseModel):
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    rationale: str = ""
    degraded: bool = False  # 是否走了模板兜底


def _fallback_plan(topic: str, max_sub_questions: int) -> PlanResult:
    sub_questions = [
        SubQuestion(
            qid=f"q{i}",
            text=f"{topic} 的{angle}",
            rationale="规划失败时的通用兜底侧面",
            initial_queries=[f"{topic} {angle}"],
        )
        for i, angle in enumerate(_FALLBACK_ANGLES[:max_sub_questions], 1)
    ]
    queries = [q.initial_queries[0] for q in sub_questions[:3]]
    return PlanResult(
        sub_questions=sub_questions,
        queries=queries,
        rationale="LLM 规划失败，使用通用子问题模板兜底",
        degraded=True,
    )


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


async def plan(
    topic: str,
    llm: LLMClient,
    *,
    max_sub_questions: int = 4,
    queries_per_round: int = 3,
) -> PlanResult:
    """规划子问题与首轮检索词。**不抛异常**，失败退模板。"""
    prompt = PLANNER_PROMPT.substitute(
        topic=topic,
        max_sub_questions=max_sub_questions,
        queries_per_round=queries_per_round,
    )

    try:
        output = await llm.complete_json(prompt, PlannerOutput)
    except LLMError as exc:
        logger.warning("规划失败，使用模板兜底: %s", exc)
        return _fallback_plan(topic, max_sub_questions)

    # LLM 可能一个子问题都没给（返回了空结构），那也走兜底
    texts = [q.text.strip() for q in output.sub_questions if q.text.strip()]
    if not texts:
        logger.warning("规划返回了空子问题列表，使用模板兜底")
        return _fallback_plan(topic, max_sub_questions)

    sub_questions: list[SubQuestion] = []
    for i, item in enumerate(output.sub_questions[:max_sub_questions], 1):
        text = item.text.strip()
        if not text:
            continue
        sub_questions.append(
            SubQuestion(
                qid=f"q{i}",
                text=text,
                rationale=item.rationale.strip(),
                initial_queries=_dedupe_keep_order(item.queries),
            )
        )

    # 检索词来源有二：顶层 queries 和每个子问题自己的 queries。
    # 顶层优先（它是 LLM 按重要性排过序的），不够就用子问题的补齐。
    queries = _dedupe_keep_order(list(output.queries))
    if not queries:
        for sub in sub_questions:
            queries.extend(sub.initial_queries)
        queries = _dedupe_keep_order(queries)

    if not queries:
        # 有子问题却一个检索词都没有 —— 用子问题文本本身当检索词，总比不搜强
        queries = [q.text for q in sub_questions[:queries_per_round]]

    return PlanResult(
        sub_questions=sub_questions,
        queries=queries[:queries_per_round],
        rationale=output.rationale.strip(),
    )


__all__ = ["PlanResult", "PlannerOutput", "plan"]
