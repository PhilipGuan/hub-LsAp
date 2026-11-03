"""finalize 节点：确定性组装 WorkflowOutput（原实现由代码拼装的最终 JSON）。

覆盖原实现的全部终止路径：
1. 成功（execution + answer 齐备）
2. 选表阶段澄清 / 无法确定表
3. 选表反馈重试后仍非法
4. 改写 Agent cannot_fix
5. 修复预算耗尽
"""

from __future__ import annotations

import logging

from ..graph_state import AgentState
from ..models import WorkflowOutput
from ..routing import selection_problems

logger = logging.getLogger(__name__)


def make_finalize_node(catalog):
    def finalize(state: AgentState) -> dict:
        question = state["question"]
        selection = state.get("selection")
        execution = state.get("execution")
        validation = state.get("validation")
        answer = state.get("answer")
        draft = state.get("draft")
        repair = state.get("repair_attempts", 0)
        tables = selection.tables if selection else []
        sql = state.get("sql")

        if execution is not None and answer is not None:
            output = WorkflowOutput(
                question=question,
                answer=answer.answer,
                sql=sql,
                tables=tables,
                row_count=execution.row_count,
                truncated=execution.truncated,
                repair_attempts=repair,
                evidence=answer.evidence,
                caveats=answer.caveats,
            )
        elif selection is not None and (
            selection.needs_clarification or not selection.tables
        ):
            output = WorkflowOutput(
                question=question,
                answer=selection.clarification_question
                or "无法从当前数据库确定回答该问题所需的表。",
                tables=tables,
            )
        elif selection is not None and selection_problems(selection.tables, catalog):
            # 选表反馈重试后仍非法（原实现转为澄清式提示）。
            problems = "；".join(selection_problems(selection.tables, catalog))
            output = WorkflowOutput(
                question=question,
                answer=f"无法确定有效的查询表：{problems}",
                tables=tables,
            )
        elif draft is not None and draft.cannot_fix:
            output = WorkflowOutput(
                question=question,
                answer=f"当前数据库无法回答该问题：{draft.explanation}",
                sql=sql,
                tables=tables,
                repair_attempts=repair,
                error=validation.message if validation else "unknown error",
            )
        else:
            # 修复预算耗尽（或其他未预期失败）。
            output = WorkflowOutput(
                question=question,
                answer="无法生成可执行的 SQL，已达到最大修复次数。",
                sql=sql,
                tables=tables,
                repair_attempts=repair,
                error=validation.message if validation else "unknown error",
            )

        logger.info(
            "stage=final answer=%s evidence=%d caveats=%d error=%s",
            " ".join(str(output.answer).split())[:500],
            len(output.evidence),
            len(output.caveats),
            output.error,
        )
        return {"output": output}

    return finalize
