"""显式代码编排：选表 → 生成 → 校验/执行 → 改写 → 汇总。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

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
    QueryState,
    SQLAttempt,
    SQLDraft,
    TableSelection,
    ValidationResult,
    WorkflowOutput,
)

logger = logging.getLogger(__name__)


def _preview(text: str, limit: int = 2000) -> str:
    """日志里把多行文本压成一行，并限制长度。"""

    compact = " ".join(str(text).split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


class SQLAgentWorkflow:
    """用 Python 状态机控制 LangChain 结构化输出链。"""

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
        self.catalog = catalog or get_catalog()
        self.validator = validator or SQLValidator(self.catalog.db_path)
        self.executor = executor or SQLExecutor(self.catalog.db_path)
        self.max_repair_attempts = (
            settings.max_repair_attempts
            if max_repair_attempts is None
            else max_repair_attempts
        )
        # 每个 workflow 实例只创建一次链，后续请求复用模型客户端。
        self.table_selector_chain = (
            table_selector_chain or build_table_selector_chain()
        )
        self.sql_generator_chain = sql_generator_chain or build_sql_generator_chain()
        self.sql_rewriter_chain = sql_rewriter_chain or build_sql_rewriter_chain()
        self.result_summarizer_chain = (
            result_summarizer_chain or build_result_summarizer_chain()
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
            _preview(question, 300),
            settings.primary_model,
            settings.summary_model,
        )
        try:
            result = await self._run(question)
            logger.info(
                "workflow finished elapsed=%.2fs tables=%s rows=%s repairs=%s error=%s",
                time.perf_counter() - started,
                result.tables,
                result.row_count,
                result.repair_attempts,
                result.error,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - CLI 需要稳定输出
            logger.exception("SQL agent workflow failed")
            return WorkflowOutput(
                question=question,
                answer=f"处理失败：{exc}",
                error=str(exc),
            )

    async def _run(self, question: str) -> WorkflowOutput:
        state = QueryState(question=question)

        selection = await self._select_tables(question)
        state.selection = selection
        if selection.needs_clarification or not selection.tables:
            logger.warning(
                "stage=table_select clarification required question=%s",
                selection.clarification_question,
            )
            return WorkflowOutput(
                question=question,
                answer=selection.clarification_question
                or "无法从当前数据库确定回答该问题所需的表。",
                tables=selection.tables,
            )

        tables = selection.tables
        state.schema_context = self.catalog.render_schema(tables)
        logger.info(
            "stage=table_selected tables=%s schema_chars=%d reason=%s",
            tables,
            len(state.schema_context),
            _preview(selection.reason, 300),
        )

        draft = await self._generate_sql(question, state.schema_context)
        sql = draft.sql
        state.sql = sql
        state.attempts.append(SQLAttempt(sql=sql, source="generator"))
        logger.info("stage=sql_generated sql=%s", _preview(sql))

        execution: ExecutionResult | None = None
        validation: ValidationResult | None = None

        # 初始 SQL + 最多 max_repair_attempts 次改写。
        for repair_index in range(self.max_repair_attempts + 1):
            validate_started = time.perf_counter()
            validation = self.validator.validate(sql)
            logger.info(
                "stage=validate attempt=%d ok=%s code=%s elapsed=%.3fs message=%s",
                repair_index + 1,
                validation.ok,
                validation.code,
                time.perf_counter() - validate_started,
                _preview(validation.message, 500),
            )
            if validation.ok:
                execute_started = time.perf_counter()
                try:
                    execution = self.executor.execute(validation.normalized_sql)
                    sql = validation.normalized_sql
                    logger.info(
                        "stage=execute ok=true rows=%s truncated=%s elapsed=%.3fs",
                        execution.row_count,
                        execution.truncated,
                        time.perf_counter() - execute_started,
                    )
                    break
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
                        normalized_sql=sql,
                    )

            state.validation = validation
            state.attempts[-1].code = validation.code
            state.attempts[-1].error = validation.message

            if repair_index >= self.max_repair_attempts:
                logger.warning(
                    "stage=repair_exhausted attempts=%d code=%s",
                    state.repair_attempts,
                    validation.code,
                )
                return WorkflowOutput(
                    question=question,
                    answer="无法生成可执行的 SQL，已达到最大修复次数。",
                    sql=sql,
                    tables=tables,
                    repair_attempts=state.repair_attempts,
                    error=validation.message,
                )

            draft = await self._rewrite_sql(
                question=question,
                schema=state.schema_context,
                sql=sql,
                validation=validation,
                attempts=state.attempts,
                attempt_number=repair_index + 1,
            )
            if draft.cannot_fix:
                logger.warning(
                    "stage=rewrite_cannot_fix attempt=%d reason=%s",
                    repair_index + 1,
                    _preview(draft.explanation, 500),
                )
                return WorkflowOutput(
                    question=question,
                    answer=f"当前数据库无法回答该问题：{draft.explanation}",
                    sql=sql,
                    tables=tables,
                    repair_attempts=state.repair_attempts,
                    error=validation.message,
                )

            state.repair_attempts += 1
            sql = draft.sql
            state.sql = sql
            state.attempts.append(SQLAttempt(sql=sql, source="rewriter"))
            logger.info(
                "stage=sql_rewritten attempt=%d sql=%s",
                state.repair_attempts,
                _preview(sql),
            )

        if execution is None:
            return WorkflowOutput(
                question=question,
                answer="SQL 执行失败。",
                sql=sql,
                tables=tables,
                repair_attempts=state.repair_attempts,
                error=(validation.message if validation else "unknown error"),
            )

        state.execution = execution

        # 空结果不重试，也不让模型猜测原因。
        if execution.row_count == 0:
            logger.info("stage=summarize skipped reason=empty_result")
            final = FinalAnswer(
                answer="未查询到符合条件的数据。",
                caveats=["SQL 执行成功，但结果集为空。"],
            )
        else:
            final = await self._summarize(question, sql, execution)

        state.answer = final
        logger.info(
            "stage=final answer=%s evidence=%d caveats=%d",
            _preview(final.answer, 500),
            len(final.evidence),
            len(final.caveats),
        )
        return WorkflowOutput(
            question=question,
            answer=final.answer,
            sql=sql,
            tables=tables,
            row_count=execution.row_count,
            truncated=execution.truncated,
            repair_attempts=state.repair_attempts,
            evidence=final.evidence,
            caveats=final.caveats,
        )

    async def _select_tables(self, question: str) -> TableSelection:
        feedback = ""
        selection: TableSelection | None = None

        for attempt in range(1, 3):
            prompt_values = {
                "question": question,
                "catalog": self.catalog.render_catalog(),
            }
            if feedback:
                prompt_values["catalog"] += (
                    f"\n\n上一次选择的问题：\n{feedback}\n请修正后重新选择。"
                )

            started = time.perf_counter()
            selection = await self.table_selector_chain.ainvoke(prompt_values)
            if not isinstance(selection, TableSelection):
                raise TypeError("TableSelectAgent 返回了非 TableSelection 结果")

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

            if selection.needs_clarification:
                return selection

            problems = self._selection_problems(selection.tables)
            if not problems:
                return selection
            feedback = "；".join(problems)
            logger.warning(
                "stage=table_select_invalid attempt=%d problems=%s",
                attempt,
                _preview(feedback, 300),
            )

        assert selection is not None
        return selection.model_copy(
            update={
                "needs_clarification": True,
                "clarification_question": f"无法确定有效的查询表：{feedback}",
            }
        )

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

    async def _generate_sql(self, question: str, schema: str) -> SQLDraft:
        started = time.perf_counter()
        draft = await self.sql_generator_chain.ainvoke(
            {"question": question, "schema": schema}
        )
        if not isinstance(draft, SQLDraft):
            raise TypeError("SQLGeneratorAgent 返回了非 SQLDraft 结果")
        logger.info(
            "stage=sql_generate elapsed=%.2fs assumptions=%d explanation=%s",
            time.perf_counter() - started,
            len(draft.assumptions),
            _preview(draft.explanation, 300),
        )
        return draft

    async def _rewrite_sql(
        self,
        *,
        question: str,
        schema: str,
        sql: str,
        validation: ValidationResult,
        attempts: list[SQLAttempt],
        attempt_number: int,
    ) -> SQLDraft:
        history = "\n".join(
            f"- [{item.source}] {item.sql}"
            + (f" | {item.code}: {item.error}" if item.error else "")
            for item in attempts[-5:]
        )
        prompt_values = {
            "question": question,
            "schema": schema,
            "sql": sql,
            "error_code": validation.code.value if validation.code else "",
            "error_message": validation.message,
            "attempt_number": attempt_number,
            "max_attempts": self.max_repair_attempts,
            "history": history,
        }
        logger.info(
            "stage=sql_rewrite_started attempt=%d/%d code=%s",
            attempt_number,
            self.max_repair_attempts,
            validation.code,
        )
        started = time.perf_counter()
        draft = await self.sql_rewriter_chain.ainvoke(prompt_values)
        if not isinstance(draft, SQLDraft):
            raise TypeError("SQLRewriteAgent 返回了非 SQLDraft 结果")
        logger.info(
            "stage=sql_rewrite_finished elapsed=%.2fs cannot_fix=%s sql=%s",
            time.perf_counter() - started,
            draft.cannot_fix,
            _preview(draft.sql),
        )
        return draft

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

