"""对外的运行入口：校验输入、调用 LangGraph、兜底异常。

流程本身全部在 ``graph.py`` 的状态图里；这里只保留 CLI 关心的职责：
空问题短路、计时与日志、把任何异常转换成固定的 ``WorkflowOutput``。
"""

from __future__ import annotations

import logging
import time

from langgraph.graph.state import CompiledStateGraph

from .config import settings
from .graph import SQLAgentGraph, recursion_limit_for
from .models import WorkflowOutput

logger = logging.getLogger(__name__)


class SQLAgentWorkflow:
    """编译一张状态图并复用；每次 ``run`` 都是独立的一次图执行。"""

    def __init__(
        self,
        *,
        compiled_graph: CompiledStateGraph | None = None,
        max_repair_attempts: int | None = None,
    ) -> None:
        self.max_repair_attempts = (
            settings.max_repair_attempts
            if max_repair_attempts is None
            else max_repair_attempts
        )
        self.recursion_limit = recursion_limit_for(
            self.max_repair_attempts, settings.max_table_attempts
        )
        self.graph = (
            SQLAgentGraph(max_repair_attempts=self.max_repair_attempts).build()
            if compiled_graph is None
            else compiled_graph
        )

    async def run(self, question: str) -> WorkflowOutput:
        if not isinstance(question, str):
            raise TypeError("question 必须是字符串")
        question = question.strip()
        if not question:
            logger.warning("workflow ignored empty question")
            return WorkflowOutput(question=question, answer="请输入要查询的问题。")

        started = time.perf_counter()
        logger.info(
            "workflow started question=%s primary_model=%s summary_model=%s "
            "recursion_limit=%d",
            " ".join(question.split())[:300],
            settings.primary_model,
            settings.summary_model,
            self.recursion_limit,
        )
        try:
            final_state = await self.graph.ainvoke(
                {"question": question, "trace": []},
                config={"recursion_limit": self.recursion_limit},
            )
            output = final_state.get("output")
            if not isinstance(output, WorkflowOutput):
                raise RuntimeError("状态图未生成 WorkflowOutput")
            logger.info(
                "workflow finished elapsed=%.2fs nodes=%d tables=%s rows=%s "
                "repairs=%s error=%s",
                time.perf_counter() - started,
                len(final_state.get("trace", [])),
                output.tables,
                output.row_count,
                output.repair_attempts,
                output.error,
            )
            return output
        except Exception as exc:  # noqa: BLE001 - CLI 需要稳定输出
            logger.exception("SQL agent workflow failed")
            return WorkflowOutput(
                question=question,
                answer=f"处理失败：{exc}",
                error=str(exc),
            )
