"""迭代终止判定。

**这是「深度研究」与「一次性问答」的分界线。** 终止不是简单地问 LLM「够了吗」——
LLM 有过早收手的已知偏差（搜两轮就说「已经充分」），所以判定按下面的顺序短路：

    1. sufficient        LLM 说够 **且** 程序覆盖率达标 **且** 无来源占比达标
    2. max_rounds_reached
    3. source_starvation 检索大面积失败且一条都没读到
    4. no_new_sources    连续 N 轮没有新增有效来源（停滞）
    5. budget_exceeded / timeout

**程序有否决权**：LLM 说够但覆盖率不达标，判定为继续。

停滞排在来源饥饿之后，是因为**检索全挂掉时两者会同时命中**，而「搜索枯竭」
这个说法会把人往「这个主题本来就没资料」上引 —— 那是一句错误的诊断。
接口在报错和主题没资料是两回事，先报更具体的那个。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .agents.reflector import ReflectionOutcome
from .config import Settings
from .models import Note, Source, StopReason, SubQuestion

logger = logging.getLogger(__name__)

# 判定「这个子问题被覆盖了」的覆盖率门槛
COVERAGE_THRESHOLD = 0.6
# 无来源结论占比超过它就不能停
MAX_UNSOURCED_RATIO = 0.30
# 检索失败率超过它且一条没读到 → 来源饥饿
STARVATION_FAILURE_RATIO = 0.50


@dataclass(slots=True)
class StopDecision:
    should_stop: bool
    reason: StopReason | None = None
    detail: str = ""


def programmatic_coverage(
    sub_questions: list[SubQuestion], notes: list[Note], valid_sids: set[str]
) -> dict[str, float]:
    """程序自己算的覆盖率 —— 不采信 LLM 的自评。

    一个子问题的覆盖率 = 它名下的笔记里，**有多少条来自可引用来源**，
    按笔记条数饱和计数。一条都没有就是 0。

    这比 LLM 打的分保守，但可复算：给定笔记就能推出同一个数。
    """
    coverage: dict[str, float] = {}
    for sub in sub_questions:
        usable = len([n for n in notes if n.qid == sub.qid and n.sid in valid_sids and not n.weak])
        # 3 条可用证据即视为覆盖充分
        coverage[sub.qid] = min(1.0, usable / 3)
    return coverage


def _coverage_sufficient(
    coverage: dict[str, float], sub_questions: list[SubQuestion]
) -> tuple[bool, list[str]]:
    if not sub_questions:
        return False, []
    lacking = [q.qid for q in sub_questions if coverage.get(q.qid, 0.0) < COVERAGE_THRESHOLD]
    return not lacking, lacking


def decide(
    *,
    round_no: int,
    max_rounds: int,
    reflection: ReflectionOutcome,
    coverage: dict[str, float],
    sub_questions: list[SubQuestion],
    stalled_rounds: int,
    unsourced_ratio: float,
    sources: list[Source],
    queries_ok: int,
    queries_total: int,
    elapsed_s: float,
    settings: Settings,
) -> StopDecision:
    """按序短路判定是否终止。**第一个命中的条件说了算。**"""

    # ── 1. 材料真的够了 ──────────────────────────────────────
    # 三个条件必须同时满足：LLM 说够、程序覆盖率达标、无来源结论不多。
    # LLM 单方面说够不算数。
    coverage_ok, lacking = _coverage_sufficient(coverage, sub_questions)
    if reflection.sufficient and coverage_ok and unsourced_ratio <= MAX_UNSOURCED_RATIO:
        return StopDecision(
            True, StopReason.sufficient, f"材料已充分（第 {round_no} 轮，LLM 与覆盖率判定一致）"
        )

    if reflection.sufficient and not coverage_ok:
        logger.info("LLM 认为材料已足够，但子问题 %s 的覆盖率不达标，继续检索", ", ".join(lacking))
    elif reflection.sufficient and unsourced_ratio > MAX_UNSOURCED_RATIO:
        logger.info(
            "LLM 认为材料已足够，但无来源结论占比 %.0f%% 偏高，继续检索",
            unsourced_ratio * 100,
        )

    # ── 2. 轮次用尽 ──────────────────────────────────────────
    if round_no >= max_rounds:
        return StopDecision(True, StopReason.max_rounds_reached, f"已达轮次上限 {max_rounds} 轮")

    # ── 3. 来源饥饿：搜了也白搜 ──────────────────────────────
    # 排在停滞之前 —— 检索全挂掉时两者会同时命中，此时说「搜索枯竭」是误诊。
    if (
        round_no >= 2
        and not any(s.readable for s in sources)
        and queries_total > 0
        and (queries_total - queries_ok) / queries_total > STARVATION_FAILURE_RATIO
    ):
        return StopDecision(
            True,
            StopReason.source_starvation,
            f"检索失败率超过 {STARVATION_FAILURE_RATIO:.0%} 且未能获取任何可用来源"
            f"（多数是接口侧问题，不是这个主题没资料）",
        )

    # ── 4. 停滞：连续多轮没有新增有效来源 ────────────────────
    if stalled_rounds >= settings.stall_patience:
        return StopDecision(
            True,
            StopReason.no_new_sources,
            f"连续 {stalled_rounds} 轮没有新增有效来源，判定为搜索枯竭",
        )

    # ── 5. 预算 / 超时 ───────────────────────────────────────
    if len(sources) >= settings.max_sources:
        return StopDecision(
            True,
            StopReason.budget_exceeded,
            f"来源数已达上限 {settings.max_sources}",
        )
    if elapsed_s >= settings.max_elapsed_s:
        return StopDecision(
            True,
            StopReason.timeout,
            f"耗时 {elapsed_s:.0f}s 已超过上限 {settings.max_elapsed_s:.0f}s",
        )

    return StopDecision(False, None, "继续下一轮")


__all__ = ["COVERAGE_THRESHOLD", "StopDecision", "decide", "programmatic_coverage"]
