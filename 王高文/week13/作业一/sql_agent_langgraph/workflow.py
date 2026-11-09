"""用 LangGraph StateGraph 编排的 SQL Agent。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from .agents import (
    build_result_summarizer_chain,
    build_sql_generator_chain,
    build_sql_rewriter_chain,
    build_table_selector_chain,
)
from .config import settings
from .db.catalog import SchemaCatalog, get_catalog
from .db.executor import SQLExecutionError, SQLExecutor
from .db.validator import SQLValidator
from .models import (
    ExecutionResult,
    FinalAnswer,
    SQLAttempt,
    SQLDraft,
    TableSelection,
    ValidationResult,
    WorkflowOutput,
)

logger = logging.getLogger(__name__)


class SQLAgentState(TypedDict, total=False):
    """LangGraph 节点间共享的单次查询状态。"""

    question: str
    catalog_context: str
    selection: TableSelection | None
    select_attempt: int
    selection_feedback: str
    schema_context: str
    sql: str
    attempts: list[SQLAttempt]
    validation: ValidationResult | None
    execution: ExecutionResult | None
    repair_attempts: int
    cannot_fix: bool
    rewrite_explanation: str
    answer: FinalAnswer | None
    output: WorkflowOutput


def _preview(text: object, limit: int = 2000) -> str:
    """日志里把多行文本压成一行，并限制长度。"""

    compact = " ".join(str(text).split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


class SQLAgentWorkflow:
    """用 LangGraph 控制 SQL Agent 的节点、分支和有界修复循环。"""

    def __init__(
        self,
        catalog: SchemaCatalog | None = None,
        validator: SQLValidator | None = None,
        executor: SQLExecutor | None = None,
        *,
        max_repair_attempts: int | None = None,
        table_selector_chain: Any | None = None,
        sql_generator_chain: Any | None = None,
        sql_rewriter_chain: Any | None = None,
        result_summarizer_chain: Any | None = None,
    ) -> None:
        self._catalog = catalog
        db_path = catalog.db_path if catalog is not None else settings.db_path
        self.validator = validator or SQLValidator(db_path)
        self.executor = executor or SQLExecutor(db_path)
        self.max_repair_attempts = (
            settings.max_repair_attempts
            if max_repair_attempts is None
            else max_repair_attempts
        )
        # 每个 workflow 实例只创建一次链，后续请求复用模型客户端。
        self.table_selector_chain = table_selector_chain or build_table_selector_chain()
        self.sql_generator_chain = sql_generator_chain or build_sql_generator_chain()
        self.sql_rewriter_chain = sql_rewriter_chain or build_sql_rewriter_chain()
        self.result_summarizer_chain = (
            result_summarizer_chain or build_result_summarizer_chain()
        )
        self.graph = self._build_graph()

    @property
    def catalog(self) -> SchemaCatalog:
        """首次进入 catalog 节点时加载；后续请求命中进程级缓存。"""

        if self._catalog is None:
            self._catalog = get_catalog()
        return self._catalog

    def _build_graph(self):
        """构建可检查、可异步执行的 LangGraph 状态图。"""

        builder = StateGraph(SQLAgentState)
        builder.add_node("catalog_load", self._catalog_load_node)
        builder.add_node("table_select", self._table_select_node)
        builder.add_node("selection_clarification", self._selection_clarification_node)
        builder.add_node("selection_failed", self._selection_failed_node)
        builder.add_node("sql_generate", self._sql_generate_node)
        builder.add_node("validate_execute", self._validate_execute_node)
        builder.add_node("sql_rewrite", self._sql_rewrite_node)
        builder.add_node("rewrite_failed", self._rewrite_failed_node)
        builder.add_node("repair_exhausted", self._repair_exhausted_node)
        builder.add_node("summarize", self._summarize_node)

        builder.add_edge(START, "catalog_load")
        builder.add_edge("catalog_load", "table_select")
        builder.add_conditional_edges(
            "table_select",
            self._route_after_table_select,
            {
                "retry": "table_select",
                "clarification": "selection_clarification",
                "invalid": "selection_failed",
                "generate": "sql_generate",
            },
        )
        builder.add_edge("selection_clarification", END)
        builder.add_edge("selection_failed", END)
        builder.add_edge("sql_generate", "validate_execute")
        builder.add_conditional_edges(
            "validate_execute",
            self._route_after_validation,
            {
                "summarize": "summarize",
                "rewrite": "sql_rewrite",
                "exhausted": "repair_exhausted",
            },
        )
        builder.add_conditional_edges(
            "sql_rewrite",
            self._route_after_rewrite,
            {"validate": "validate_execute", "failed": "rewrite_failed"},
        )
        builder.add_edge("rewrite_failed", END)
        builder.add_edge("repair_exhausted", END)
        builder.add_edge("summarize", END)
        return builder.compile()

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
            _preview(question, 300),
            settings.primary_model,
            settings.summary_model,
        )
        try:
            state = await self.graph.ainvoke(
                {
                    "question": question,
                    "select_attempt": 0,
                    "selection_feedback": "",
                    "attempts": [],
                    "repair_attempts": 0,
                    "cannot_fix": False,
                }
            )
            output = state.get("output")
            if not isinstance(output, WorkflowOutput):
                raise TypeError("LangGraph 工作流未生成 WorkflowOutput")
            logger.info(
                "workflow finished elapsed=%.2fs tables=%s rows=%s repairs=%s error=%s",
                time.perf_counter() - started,
                output.tables,
                output.row_count,
                output.repair_attempts,
                output.error,
            )
            return output
        except Exception as exc:  # noqa: BLE001 - CLI 需要稳定输出
            logger.exception("SQL agent graph failed")
            return WorkflowOutput(
                question=question,
                answer=f"处理失败：{exc}",
                error=str(exc),
            )

    def _catalog_load_node(self, state: SQLAgentState) -> dict[str, Any]:
        catalog = self.catalog
        logger.info("stage=catalog_ready tables=%d", len(catalog.table_names))
        return {"catalog_context": catalog.render_catalog()}

    async def _table_select_node(self, state: SQLAgentState) -> dict[str, Any]:
        attempt = state.get("select_attempt", 0) + 1
        catalog = state.get("catalog_context") or self.catalog.render_catalog()
        feedback = state.get("selection_feedback", "")
        if feedback:
            catalog += f"\n\n上一次选择的问题：\n{feedback}\n请修正后重新选择。"

        started = time.perf_counter()
        selection = await self.table_selector_chain.ainvoke(
            {"question": state["question"], "catalog": catalog}
        )
        if not isinstance(selection, TableSelection):
            raise TypeError("TableSelectAgent 返回了非 TableSelection 结果")

        problems = [] if selection.needs_clarification else self._selection_problems(
            selection.tables
        )
        logger.info(
            "stage=table_select attempt=%d tables=%s confidence=%.2f "
            "needs_clarification=%s elapsed=%.2fs reason=%s",
            attempt,
            selection.tables,
            selection.confidence,
            selection.needs_clarification,
            time.perf_counter() - started,
            _preview(selection.reason, 300),
        )
        if problems:
            logger.warning(
                "stage=table_select_invalid attempt=%d problems=%s",
                attempt,
                _preview("；".join(problems), 300),
            )

        return {
            "selection": selection,
            "select_attempt": attempt,
            "selection_feedback": "；".join(problems),
        }

    def _route_after_table_select(
        self, state: SQLAgentState
    ) -> Literal["retry", "clarification", "invalid", "generate"]:
        selection = state.get("selection")
        if selection is None:
            return "invalid"
        if selection.needs_clarification:
            return "clarification"
        problems = self._selection_problems(selection.tables)
        if problems and state.get("select_attempt", 0) < 2:
            return "retry"
        if problems:
            return "invalid"
        return "generate"

    def _selection_clarification_node(self, state: SQLAgentState) -> dict[str, Any]:
        selection = state.get("selection")
        question = (
            selection.clarification_question
            if selection is not None
            else None
        ) or "无法从当前数据库确定回答该问题所需的表。"
        logger.warning("stage=table_select clarification required question=%s", question)
        return {
            "output": WorkflowOutput(
                question=state["question"],
                answer=question,
                tables=selection.tables if selection else [],
            )
        }

    def _selection_failed_node(self, state: SQLAgentState) -> dict[str, Any]:
        selection = state.get("selection")
        feedback = state.get("selection_feedback", "未选择任何表")
        answer = f"无法确定有效的查询表：{feedback}"
        return {
            "output": WorkflowOutput(
                question=state["question"],
                answer=answer,
                tables=selection.tables if selection else [],
                error=feedback,
            )
        }

    def _selection_problems(self, tables: list[str]) -> list[str]:
        if not tables:
            return ["未选择任何表"]
        unknown = sorted(set(tables) - set(self.catalog.table_names))
        if unknown:
            return [
                f"表不存在：{', '.join(unknown)}；"
                f"可选表：{', '.join(self.catalog.table_names)}"
            ]
        if not self.catalog.is_connected(tables):
            return ["所选表之间没有外键连接，请重新选择可以 JOIN 的表"]
        return []

    async def _sql_generate_node(self, state: SQLAgentState) -> dict[str, Any]:
        selection = state.get("selection")
        if selection is None:
            raise ValueError("生成 SQL 前缺少表选择结果")
        schema = self.catalog.render_schema(selection.tables)
        started = time.perf_counter()
        draft = await self.sql_generator_chain.ainvoke(
            {"question": state["question"], "schema": schema}
        )
        if not isinstance(draft, SQLDraft):
            raise TypeError("SQLGeneratorAgent 返回了非 SQLDraft 结果")
        logger.info(
            "stage=sql_generate elapsed=%.2fs assumptions=%d explanation=%s",
            time.perf_counter() - started,
            len(draft.assumptions),
            _preview(draft.explanation, 300),
        )
        logger.info("stage=sql_generated sql=%s", _preview(draft.sql))
        return {
            "schema_context": schema,
            "sql": draft.sql,
            "attempts": [SQLAttempt(sql=draft.sql, source="generator")],
        }

    def _validate_execute_node(self, state: SQLAgentState) -> dict[str, Any]:
        sql = state.get("sql", "")
        started = time.perf_counter()
        validation = self.validator.validate(sql)
        logger.info(
            "stage=validate attempt=%d ok=%s code=%s elapsed=%.3fs message=%s",
            state.get("repair_attempts", 0) + 1,
            validation.ok,
            validation.code,
            time.perf_counter() - started,
            _preview(validation.message, 500),
        )
        if validation.ok:
            execute_started = time.perf_counter()
            try:
                execution = self.executor.execute(validation.normalized_sql)
                logger.info(
                    "stage=execute ok=true rows=%s truncated=%s elapsed=%.3fs",
                    execution.row_count,
                    execution.truncated,
                    time.perf_counter() - execute_started,
                )
                return {
                    "sql": validation.normalized_sql,
                    "validation": validation,
                    "execution": execution,
                }
            except SQLExecutionError as exc:
                logger.warning(
                    "stage=execute ok=false code=%s elapsed=%.3fs message=%s",
                    exc.code,
                    time.perf_counter() - execute_started,
                    _preview(str(exc), 500),
                )
                validation = ValidationResult(
                    ok=False,
                    code=exc.code,
                    message=str(exc),
                    normalized_sql=validation.normalized_sql,
                )

        attempts = list(state.get("attempts", []))
        if attempts:
            attempts[-1] = attempts[-1].model_copy(
                update={"code": validation.code, "error": validation.message}
            )
        return {"validation": validation, "execution": None, "attempts": attempts}

    def _route_after_validation(
        self, state: SQLAgentState
    ) -> Literal["summarize", "rewrite", "exhausted"]:
        if state.get("execution") is not None:
            return "summarize"
        if state.get("repair_attempts", 0) >= self.max_repair_attempts:
            return "exhausted"
        return "rewrite"

    async def _sql_rewrite_node(self, state: SQLAgentState) -> dict[str, Any]:
        validation = state.get("validation")
        if validation is None:
            raise ValueError("修复 SQL 前缺少校验或执行错误")
        attempts = state.get("attempts", [])
        attempt_number = state.get("repair_attempts", 0) + 1
        history = "\n".join(
            f"- [{item.source}] {item.sql}"
            + (f" | {item.code}: {item.error}" if item.error else "")
            for item in attempts[-5:]
        )
        started = time.perf_counter()
        draft = await self.sql_rewriter_chain.ainvoke(
            {
                "question": state["question"],
                "schema": state.get("schema_context", ""),
                "sql": state.get("sql", ""),
                "error_code": validation.code.value if validation.code else "",
                "error_message": validation.message,
                "attempt_number": attempt_number,
                "max_attempts": self.max_repair_attempts,
                "history": history,
            }
        )
        if not isinstance(draft, SQLDraft):
            raise TypeError("SQLRewriteAgent 返回了非 SQLDraft 结果")
        logger.info(
            "stage=sql_rewrite_finished attempt=%d elapsed=%.2fs cannot_fix=%s sql=%s",
            attempt_number,
            time.perf_counter() - started,
            draft.cannot_fix,
            _preview(draft.sql),
        )
        if draft.cannot_fix:
            return {"cannot_fix": True, "rewrite_explanation": draft.explanation}

        attempts = list(attempts)
        attempts.append(SQLAttempt(sql=draft.sql, source="rewriter"))
        logger.info("stage=sql_rewritten attempt=%d sql=%s", attempt_number, _preview(draft.sql))
        return {
            "sql": draft.sql,
            "attempts": attempts,
            "repair_attempts": attempt_number,
            "cannot_fix": False,
        }

    def _route_after_rewrite(self, state: SQLAgentState) -> Literal["validate", "failed"]:
        return "failed" if state.get("cannot_fix") else "validate"

    def _rewrite_failed_node(self, state: SQLAgentState) -> dict[str, Any]:
        validation = state.get("validation")
        explanation = state.get("rewrite_explanation", "未提供原因")
        selection = state.get("selection")
        return {
            "output": WorkflowOutput(
                question=state["question"],
                answer=f"当前数据库无法回答该问题：{explanation}",
                sql=state.get("sql"),
                tables=selection.tables if selection else [],
                repair_attempts=state.get("repair_attempts", 0),
                error=validation.message if validation else explanation,
            )
        }

    def _repair_exhausted_node(self, state: SQLAgentState) -> dict[str, Any]:
        validation = state.get("validation")
        selection = state.get("selection")
        message = validation.message if validation else "未知错误"
        logger.warning(
            "stage=repair_exhausted attempts=%d code=%s",
            state.get("repair_attempts", 0),
            validation.code if validation else None,
        )
        return {
            "output": WorkflowOutput(
                question=state["question"],
                answer="无法生成可执行的 SQL，已达到最大修复次数。",
                sql=state.get("sql"),
                tables=selection.tables if selection else [],
                repair_attempts=state.get("repair_attempts", 0),
                error=message,
            )
        }

    async def _summarize_node(self, state: SQLAgentState) -> dict[str, Any]:
        selection = state.get("selection")
        execution = state.get("execution")
        if selection is None or execution is None:
            raise ValueError("汇总前缺少表选择或 SQL 执行结果")
        sql = state.get("sql", "")
        if execution.row_count == 0:
            logger.info("stage=summarize skipped reason=empty_result")
            final = FinalAnswer(
                answer="未查询到符合条件的数据。",
                caveats=["SQL 执行成功，但结果集为空。"],
            )
        else:
            final = await self._summarize(state["question"], sql, execution)

        logger.info(
            "stage=final answer=%s evidence=%d caveats=%d",
            _preview(final.answer, 500),
            len(final.evidence),
            len(final.caveats),
        )
        output = WorkflowOutput(
            question=state["question"],
            answer=final.answer,
            sql=sql,
            tables=selection.tables,
            row_count=execution.row_count,
            columns=execution.columns,
            rows=execution.rows,
            truncated=execution.truncated,
            repair_attempts=state.get("repair_attempts", 0),
            evidence=final.evidence,
            caveats=final.caveats,
        )
        return {"answer": final, "output": output}

    async def _summarize(
        self, question: str, sql: str, execution: ExecutionResult
    ) -> FinalAnswer:
        rows = execution.rows[: settings.max_summary_rows]
        prompt_values = {
            "question": question,
            "sql": sql,
            "columns_json": json.dumps(execution.columns, ensure_ascii=False),
            "row_count": execution.row_count,
            "truncated": "是" if execution.truncated else "否",
            "max_rows": settings.max_summary_rows,
            "rows_json": json.dumps(rows, ensure_ascii=False),
        }
        try:
            started = time.perf_counter()
            logger.info(
                "stage=summarize_started rows=%d truncated=%s",
                execution.row_count,
                execution.truncated,
            )
            final = await self.result_summarizer_chain.ainvoke(prompt_values)
            if not isinstance(final, FinalAnswer):
                raise TypeError("ResultSummaryAgent 返回了非 FinalAnswer 结果")
            logger.info(
                "stage=summarize_finished elapsed=%.2fs evidence=%d caveats=%d",
                time.perf_counter() - started,
                len(final.evidence),
                len(final.caveats),
            )
            return final
        except Exception as exc:  # noqa: BLE001 - 汇总失败时降级输出
            logger.warning("stage=summarize_failed fallback=table error=%s", exc)
            return self._fallback_summary(execution, exc)

    def _fallback_summary(
        self, execution: ExecutionResult, error: Exception
    ) -> FinalAnswer:
        if not execution.rows:
            return FinalAnswer(answer="未查询到符合条件的数据。")

        header = "| " + " | ".join(execution.columns) + " |"
        divider = "| " + " | ".join("---" for _ in execution.columns) + " |"
        body = [
            "| " + " | ".join(value.replace("|", "\\|") for value in row) + " |"
            for row in execution.rows[: settings.max_summary_rows]
        ]
        note = (
            f"仅展示前 {settings.max_summary_rows} 行。"
            if execution.truncated or execution.row_count > settings.max_summary_rows
            else ""
        )
        return FinalAnswer(
            answer="\n".join(
                [f"查询返回 {execution.row_count} 行。", header, divider, *body, note]
            ),
            caveats=[f"结果汇总 Agent 调用失败，已降级为表格输出：{error}"],
        )

