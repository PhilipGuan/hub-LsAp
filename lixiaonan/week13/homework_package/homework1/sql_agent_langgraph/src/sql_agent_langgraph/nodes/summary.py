"""summarize 节点：把执行结果转成自然语言（原 ResultSummaryAgent）。

两个确定性分支先于 LLM：
- 空结果：不调 LLM，如实回答（原实现：空结果不重试、不让模型猜原因）；
- LLM 失败：降级为 Markdown 表格输出。
"""

from __future__ import annotations

import json
import logging
import time

from ..graph_state import AgentState
from ..models import ExecutionResult, FinalAnswer

logger = logging.getLogger(__name__)


def _fallback_summary(
    execution: ExecutionResult, max_summary_rows: int, error: Exception
) -> FinalAnswer:
    """原实现 _fallback_summary 的等价实现。"""

    if not execution.rows:
        return FinalAnswer(answer="未查询到符合条件的数据。")

    header = "| " + " | ".join(execution.columns) + " |"
    divider = "| " + " | ".join("---" for _ in execution.columns) + " |"
    body = [
        "| " + " | ".join(value.replace("|", "\\|") for value in row) + " |"
        for row in execution.rows[:max_summary_rows]
    ]
    note = (
        f"仅展示前 {max_summary_rows} 行。"
        if execution.truncated or execution.row_count > max_summary_rows
        else ""
    )
    return FinalAnswer(
        answer="\n".join(
            [f"查询返回 {execution.row_count} 行。", header, divider, *body, note]
        ),
        caveats=[f"结果汇总 Agent 调用失败，已降级为表格输出：{error}"],
    )


def make_summarize_node(summary_llm, max_summary_rows: int = 50):
    def summarize(state: AgentState) -> dict:
        execution = state["execution"]
        sql = state.get("sql", "")

        if execution.row_count == 0:
            logger.info("stage=summarize skipped reason=empty_result")
            return {
                "answer": FinalAnswer(
                    answer="未查询到符合条件的数据。",
                    caveats=["SQL 执行成功，但结果集为空。"],
                )
            }

        rows = execution.rows[:max_summary_rows]
        prompt = (
            "用户问题：\n"
            f"<question>\n{state['question']}\n</question>\n\n"
            f"最终 SQL：\n{sql}\n\n"
            f"列名：{json.dumps(execution.columns, ensure_ascii=False)}\n"
            f"返回行数：{execution.row_count}\n"
            f"是否截断：{'是' if execution.truncated else '否'}\n"
            f"结果行（最多 {max_summary_rows} 行）：\n"
            f"{json.dumps(rows, ensure_ascii=False)}\n"
        )
        try:
            started = time.perf_counter()
            logger.info(
                "stage=summarize_started rows=%d truncated=%s prompt_chars=%d",
                execution.row_count,
                execution.truncated,
                len(prompt),
            )
            final = summary_llm.invoke(prompt)
            if not isinstance(final, FinalAnswer):
                raise TypeError("ResultSummaryAgent 返回了非 FinalAnswer 结果")
            logger.info(
                "stage=summarize_finished elapsed=%.2fs evidence=%d caveats=%d",
                time.perf_counter() - started,
                len(final.evidence),
                len(final.caveats),
            )
            return {"answer": final}
        except Exception as exc:  # noqa: BLE001 - 汇总失败时降级输出
            logger.warning("stage=summarize_failed fallback=table error=%s", exc)
            return {"answer": _fallback_summary(execution, max_summary_rows, exc)}

    return summarize
