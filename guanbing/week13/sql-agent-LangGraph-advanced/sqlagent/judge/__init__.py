"""逐层裁判装置：把「Agent 靠不靠谱」变成一张可定位的表。

两层划分（第一性原理：可判定 vs 需判断）：

- `deterministic.py` —— L0/L1/L3/L4 的**硬判据**（结果集相等、能否执行、闸门标签）
  与 L5 数字溯源**诊断**；纯代码，零 LLM 调用。
- `llm.py` —— L2（SQL 语义等价）、L1 增强（多余表是否无害）、L4 增强（结果集语义复核）、
  L5（要点覆盖）；必带参照物。

`JudgeAgent` 把 6 层跑一遍并聚合端到端结论。判定语义：
`pass` 为 `True`/`False` 表示「有判定」；`None` 表示「裁判未给出结论」（如解析失败 / LLM 关闭），
不计入端到端通过性，但会在明细里显式暴露，不掩盖问题。
"""

from __future__ import annotations

from .deterministic import (judge_guard, judge_number_trace, judge_result,
                            judge_scope, judge_tables, normalize_rows, row_equal)
from .llm import judge_answer, judge_result_llm, judge_sql, judge_tables_llm

# 5 层裁判的层级顺序（用于明细表列序）
LAYER_NAMES = ["L0_scope", "L1_tables", "L2_sql", "L3_guard", "L4_result", "L5_answer"]


class JudgeAgent:
    """逐层裁判：对同一道题的 trace 依次跑 L0..L5，最后聚合端到端结论。

    两处「确定性 → LLM」升级：
    - L1：确定性命中但选多了表 → 交 LLM 判多余表是否无害；
    - L4：确定性结果集不一致（多为形状/列数不同）→ 交 LLM 做语义等价复核。
    """

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm

    def judge(self, item, trace) -> dict:
        layers = {
            "L0_scope": judge_scope(item, trace),
            "L1_tables": judge_tables(item, trace),
            "L2_sql": judge_sql(item, trace) if self.use_llm
                      else {"pass": None, "detail": "LLM 关闭", "score": None},
            "L3_guard": judge_guard(item, trace),
            "L4_result": judge_result(item, trace),
            "L5_answer": judge_answer(item, trace) if self.use_llm
                         else {"pass": None, "detail": "LLM 关闭", "score": None},
        }

        # L1 增强：确定性命中但有「多余表」→ 交 LLM 判是否无害
        extra = set(trace.get("tables") or []) - set(item["tables"])
        if self.use_llm and extra and layers["L1_tables"].get("pass") is True:
            human = judge_tables_llm(item, trace)
            layers["L1_tables"]["detail"] += f"；多余表 {sorted(extra)}：{human.get('reason', '')}"
            if human.get("harmless") is False:
                layers["L1_tables"]["pass"] = False

        # L4 增强：确定性判不一致 → 交 LLM 做「语义等价」复核（形状不同 ≠ 结果错）
        if self.use_llm and layers["L4_result"].get("pass") is False:
            human = judge_result_llm(item, trace)
            if human.get("equivalent") is True:
                layers["L4_result"]["pass"] = True
                layers["L4_result"]["detail"] = (
                    f"严格比较不一致，但 LLM 复核语义等价：{human.get('reason', '')}")
            elif human.get("equivalent") is False:
                layers["L4_result"]["detail"] += (
                    f"；LLM 复核仍不等价：{human.get('reason', '')}")

        # L5 诊断：数字溯源（不参与 pass/fail）
        layers["L5_answer"]["suspicious"] = judge_number_trace(item, trace)["suspicious"]

        judged = [v["pass"] for v in layers.values() if v.get("pass") is not None]
        return {"id": item["id"], "question": item["question"], "scope": item["scope"],
                "layers": layers, "pass": all(judged)}


def run_and_judge(item, judge: JudgeAgent, parser, *, asker=None) -> dict:
    """对一题先跑 Agent 链路，再逐层裁判。

    默认走 `ChatBI.ask` 入口 —— 它的 trace 一定含 `scope`（闸门结论），in 题还会带上
    完整流水线结果；out / ambiguous 题则停在闸门、不生成 SQL。
    也可传入 `asker(question) -> trace` 以评测 `agents/` 路径（见 notebook）。
    每题用一个独立实例，避免对话历史在题目之间串味。
    """
    if asker is None:
        from ..chatbi import ChatBI
        asker = ChatBI(parser).ask
    trace = asker(item["question"])
    return judge.judge(item, trace)


__all__ = [
    "LAYER_NAMES", "JudgeAgent", "run_and_judge",
    "judge_guard", "judge_number_trace", "judge_result", "judge_scope", "judge_tables",
    "normalize_rows", "row_equal",
    "judge_answer", "judge_result_llm", "judge_sql", "judge_tables_llm",
]
