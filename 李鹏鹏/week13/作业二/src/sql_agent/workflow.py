"""SQL Agent 对外门面：编译 LangGraph 状态图并组装最终 JSON。"""

from __future__ import annotations

import logging
import time

from .config import settings
from .db.catalog import SchemaCatalog, get_catalog
from .db.executor import SQLExecutor
from .db.validator import SQLValidator
from .graph import WorkflowState, build_sql_agent_graph
from .logging_config import compact_text
from .models import WorkflowOutput

logger = logging.getLogger(__name__)


class SQLAgentWorkflow:
    """LangGraph 编排的 SQL Agent。

    图结构见 ``graph.py``；本类只负责依赖装配、日志和 ``WorkflowOutput`` 转换，
    不再包含手写的重试循环。
    """

    def __init__(
        self,
        catalog: SchemaCatalog | None = None,
        validator: SQLValidator | None = None,
        executor: SQLExecutor | None = None,
        *,
        max_repair_attempts: int | None = None,
    ) -> None:
        self.catalog = catalog or get_catalog()
        self.validator = validator or SQLValidator(self.catalog.db_path)
        self.executor = executor or SQLExecutor(self.catalog.db_path)
        self.max_repair_attempts = (
            settings.max_repair_attempts
            if max_repair_attempts is None
            else max_repair_attempts
        )
        self.graph = build_sql_agent_graph(
            catalog=self.catalog,
            validator=self.validator,
            executor=self.executor,
            max_repair_attempts=self.max_repair_attempts,
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
            "workflow started question=%s primary_model=%s summary_model=%s",
            compact_text(question, 300),
            settings.primary_model,
            settings.summary_model,
        )
        try:
            state = await self.graph.ainvoke(
                {
                    "question": question,
                    "attempts": [],
                    "repair_attempts": 0,
                    "select_attempts": 0,
                    "select_feedback": "",
                    "tables": [],
                    "stage": "init",
                }
            )
            result = self._to_output(question, state)
            logger.info(
                "workflow finished elapsed=%.2fs tables=%s rows=%s repairs=%s error=%s",
                time.perf_counter() - started,
                result.tables,
                result.row_count,
                result.repair_attempts,
                result.error,
            )
            return result
        except Exception as exc:  # CLI 需要稳定输出
            logger.exception("SQL agent workflow failed")
            return WorkflowOutput(
                question=question,
                answer=f"处理失败：{exc}",
                error=str(exc),
            )

    @staticmethod
    def _to_output(question: str, state: WorkflowState) -> WorkflowOutput:
        answer = state.get("answer")
        execution = state.get("execution")
        return WorkflowOutput(
            question=question,
            answer=answer.answer if answer else "未生成答案。",
            sql=state.get("sql"),
            tables=state.get("tables", []),
            columns=execution.columns if execution else [],
            rows=execution.rows if execution else [],
            row_count=execution.row_count if execution else 0,
            truncated=execution.truncated if execution else False,
            repair_attempts=state.get("repair_attempts", 0),
            evidence=answer.evidence if answer else [],
            caveats=answer.caveats if answer else [],
            error=state.get("error"),
        )
