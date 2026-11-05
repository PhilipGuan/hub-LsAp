"""LangGraph 编排：选表 -> 生成 -> 校验 -> 执行 -> 改写 -> 汇总。

条件边与循环由确定性代码控制，LLM 只负责各自的语义决策：

    START
      └─> select_tables ─> check_selection ─┬─> generate_sql ─> validate_sql
                                            ├─> select_tables（非法选择反馈重试）
                                            └─> END（需要澄清 / 无有效表）

    validate_sql ─┬─ 通过 ──> execute_sql ─┬─ 有结果 ───> summarize ──> END
                  │                        ├─ 空结果 ───> empty_result ─> END
                  │                        └─ 执行错误 ─┐
                  └─ 校验失败 ──────────────────────────┴─> rewrite_sql
                                                             ├─> validate_sql
                                                             └─> END（无法修复 / 预算耗尽）
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .agents import generate_sql, rewrite_sql, select_tables, summarize
from .config import settings
from .db.catalog import SchemaCatalog
from .db.executor import SQLExecutionError, SQLExecutor
from .db.validator import SQLValidator
from .logging_config import compact_text
from .models import (
    ExecutionResult,
    FinalAnswer,
    SQLAttempt,
    SQLDraft,
    TableSelection,
    ValidationResult,
)

logger = logging.getLogger(__name__)

MAX_SELECT_ATTEMPTS = 2


class WorkflowState(TypedDict, total=False):
    """LangGraph 在各节点之间传递的状态。

    字段与 ``models.QueryState`` 对齐；``answer``/``error`` 由终止节点写入，
    最终再由 ``SQLAgentWorkflow`` 组装成 ``WorkflowOutput``。
    """

    question: str
    selection: TableSelection
    schema_context: str
    sql: str
    attempts: list[SQLAttempt]
    validation: ValidationResult
    execution: ExecutionResult
    answer: FinalAnswer
    tables: list[str]
    repair_attempts: int
    select_attempts: int
    select_feedback: str
    stage: str
    error: str


def _selection_problems(catalog: SchemaCatalog, tables: list[str]) -> list[str]:
    """返回选表结果中的确定性问题；空列表表示通过。"""

    if not tables:
        return ["未选择任何表"]

    unknown = sorted(set(tables) - set(catalog.table_names))
    if unknown:
        return [
            (
                f"表不存在：{', '.join(unknown)}；"
                f"可选表：{', '.join(catalog.table_names)}"
            )
        ]

    if not catalog.is_connected(tables):
        return ["所选表之间没有外键连接，请重新选择可以 JOIN 的表"]
    return []


def _attempt_history(attempts: list[SQLAttempt]) -> str:
    """把最近的尝试压成改写 Agent 可读的文本。"""

    return "\n".join(
        f"- [{item.source}] {item.sql}"
        + (f" | {item.code}: {item.error}" if item.error else "")
        for item in attempts[-5:]
    )


def _update_last_attempt(
    attempts: list[SQLAttempt], validation: ValidationResult
) -> list[SQLAttempt]:
    """把校验/执行错误记到最后一次 SQL 尝试上。"""

    updated = list(attempts)
    if updated:
        updated[-1] = updated[-1].model_copy(
            update={"code": validation.code, "error": validation.message}
        )
    return updated


async def _summarize_with_fallback(
    question: str, sql: str, execution: ExecutionResult
) -> FinalAnswer:
    """调用汇总 Agent；失败时降级为 Markdown 表格。"""

    try:
        started = time.perf_counter()
        logger.info(
            "stage=summarize_started rows=%d truncated=%s",
            execution.row_count,
            execution.truncated,
        )
        final = await summarize(
            question=question,
            sql=sql,
            columns=execution.columns,
            rows=execution.rows[: settings.max_summary_rows],
            row_count=execution.row_count,
            truncated=execution.truncated,
        )
        logger.info(
            "stage=summarize_finished elapsed=%.2fs evidence=%d caveats=%d",
            time.perf_counter() - started,
            len(final.evidence),
            len(final.caveats),
        )
        return final
    except Exception as exc:  # noqa: BLE001 - 汇总失败时降级输出
        logger.warning("stage=summarize_failed fallback=table error=%s", exc)
        return _fallback_summary(execution, exc)


def _fallback_summary(execution: ExecutionResult, error: Exception) -> FinalAnswer:
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


def build_sql_agent_graph(
    *,
    catalog: SchemaCatalog,
    validator: SQLValidator,
    executor: SQLExecutor,
    max_repair_attempts: int | None = None,
) -> CompiledStateGraph:
    """构建并编译 SQL Agent 状态图。

    依赖通过闭包注入；节点仍调用模块级 Agent 函数，便于测试时 monkeypatch。
    """

    repair_budget = (
        settings.max_repair_attempts
        if max_repair_attempts is None
        else max_repair_attempts
    )

    async def select_tables_node(state: WorkflowState) -> dict[str, Any]:
        question = state["question"]
        attempt = state.get("select_attempts", 0) + 1
        feedback = state.get("select_feedback", "")
        feedback_text = (
            f"\n上一次选择的问题：\n{feedback}\n请修正后重新选择。\n" if feedback else ""
        )

        started = time.perf_counter()
        selection = await select_tables(
            question=question,
            catalog=catalog.render_catalog(),
            feedback=feedback_text,
        )
        logger.info(
            "stage=table_select attempt=%d tables=%s confidence=%.2f "
            "needs_clarification=%s elapsed=%.2fs reason=%s",
            attempt,
            selection.tables,
            selection.confidence,
            selection.needs_clarification,
            time.perf_counter() - started,
            compact_text(selection.reason, 300),
        )
        return {
            "selection": selection,
            "select_attempts": attempt,
            "select_feedback": "",
            "stage": "table_select",
        }

    def check_selection_node(state: WorkflowState) -> dict[str, Any]:
        selection = state["selection"]

        if selection.needs_clarification or not selection.tables:
            logger.warning(
                "stage=table_select clarification required question=%s",
                selection.clarification_question,
            )
            return {
                "tables": selection.tables,
                "answer": FinalAnswer(
                    answer=selection.clarification_question
                    or "无法从当前数据库确定回答该问题所需的表。"
                ),
                "stage": "final",
            }

        problems = _selection_problems(catalog, selection.tables)
        if not problems:
            schema_context = catalog.render_schema(selection.tables)
            logger.info(
                "stage=table_selected tables=%s schema_chars=%d reason=%s",
                selection.tables,
                len(schema_context),
                compact_text(selection.reason, 300),
            )
            return {
                "tables": selection.tables,
                "schema_context": schema_context,
                "stage": "table_selected",
            }

        if state.get("select_attempts", 0) >= MAX_SELECT_ATTEMPTS:
            feedback = "；".join(problems)
            logger.warning(
                "stage=table_select_exhausted attempts=%d problems=%s",
                state.get("select_attempts", 0),
                compact_text(feedback, 300),
            )
            return {
                "tables": selection.tables,
                "answer": FinalAnswer(answer=f"无法确定有效的查询表：{feedback}"),
                "stage": "final",
            }

        feedback = "；".join(problems)
        logger.warning(
            "stage=table_select_invalid attempt=%d problems=%s",
            state.get("select_attempts", 0),
            compact_text(feedback, 300),
        )
        return {"select_feedback": feedback, "stage": "table_select_retry"}

    def route_after_selection(state: WorkflowState) -> str:
        if state.get("answer") is not None:
            return "end"
        if state.get("select_feedback"):
            return "retry"
        return "generate"

    async def generate_sql_node(state: WorkflowState) -> dict[str, Any]:
        question = state["question"]
        schema = state["schema_context"]
        started = time.perf_counter()
        draft = await generate_sql(question=question, schema=schema)
        logger.info(
            "stage=sql_generate elapsed=%.2fs schema_chars=%d assumptions=%d explanation=%s",
            time.perf_counter() - started,
            len(schema),
            len(draft.assumptions),
            compact_text(draft.explanation, 300),
        )
        logger.info("stage=sql_generated sql=%s", compact_text(draft.sql))
        return {
            "sql": draft.sql,
            "attempts": [
                *state.get("attempts", []),
                SQLAttempt(sql=draft.sql, source="generator"),
            ],
            "stage": "sql_generated",
        }

    def validate_sql_node(state: WorkflowState) -> dict[str, Any]:
        sql = state["sql"]
        attempts = state.get("attempts", [])
        attempt_number = max(len(attempts), 1)

        started = time.perf_counter()
        validation = validator.validate(sql)
        logger.info(
            "stage=validate attempt=%d ok=%s code=%s elapsed=%.3fs message=%s",
            attempt_number,
            validation.ok,
            validation.code,
            time.perf_counter() - started,
            compact_text(validation.message, 500),
        )
        return {
            "validation": validation,
            "attempts": _update_last_attempt(list(attempts), validation),
            "stage": "validated",
        }

    def route_after_validate(state: WorkflowState) -> str:
        if state["validation"].ok:
            return "execute"
        if state.get("repair_attempts", 0) >= repair_budget:
            return "exhausted"
        return "rewrite"

    def execute_sql_node(state: WorkflowState) -> dict[str, Any]:
        sql = state["validation"].normalized_sql
        started = time.perf_counter()

        try:
            execution = executor.execute(sql)
            logger.info(
                "stage=execute ok=true rows=%s truncated=%s elapsed=%.3fs",
                execution.row_count,
                execution.truncated,
                time.perf_counter() - started,
            )
            return {"sql": sql, "execution": execution, "stage": "executed"}
        except SQLExecutionError as exc:
            logger.warning(
                "stage=execute ok=false code=%s elapsed=%.3fs message=%s",
                exc.code,
                time.perf_counter() - started,
                compact_text(str(exc), 500),
            )
            validation = ValidationResult(
                ok=False,
                code=exc.code,
                message=str(exc),
                normalized_sql=sql,
            )
            return {
                "validation": validation,
                "attempts": _update_last_attempt(list(state.get("attempts", [])), validation),
                "stage": "execute_failed",
            }

    def route_after_execute(state: WorkflowState) -> str:
        execution = state.get("execution")
        if execution is not None:
            return "empty" if execution.row_count == 0 else "summarize"
        if state.get("repair_attempts", 0) >= repair_budget:
            return "exhausted"
        return "rewrite"

    async def rewrite_sql_node(state: WorkflowState) -> dict[str, Any]:
        validation = state["validation"]
        attempt_number = state.get("repair_attempts", 0) + 1
        history = _attempt_history(list(state.get("attempts", [])))

        logger.info(
            "stage=sql_rewrite_started attempt=%d/%d code=%s",
            attempt_number,
            repair_budget,
            validation.code,
        )
        started = time.perf_counter()
        draft: SQLDraft = await rewrite_sql(
            question=state["question"],
            schema=state["schema_context"],
            sql=state["sql"],
            code=str(validation.code.value if validation.code else ""),
            message=validation.message,
            history=history,
            attempt_number=attempt_number,
            max_attempts=repair_budget,
        )
        logger.info(
            "stage=sql_rewrite_finished elapsed=%.2fs cannot_fix=%s sql=%s",
            time.perf_counter() - started,
            draft.cannot_fix,
            compact_text(draft.sql),
        )

        if draft.cannot_fix:
            logger.warning(
                "stage=rewrite_cannot_fix attempt=%d reason=%s",
                attempt_number,
                compact_text(draft.explanation, 500),
            )
            return {
                "answer": FinalAnswer(
                    answer=f"当前数据库无法回答该问题：{draft.explanation}"
                ),
                "error": validation.message,
                "stage": "final",
            }

        return {
            "sql": draft.sql,
            "attempts": [
                *state.get("attempts", []),
                SQLAttempt(sql=draft.sql, source="rewriter"),
            ],
            "repair_attempts": attempt_number,
            "stage": "sql_rewritten",
        }

    def route_after_rewrite(state: WorkflowState) -> str:
        return "end" if state.get("answer") is not None else "validate"

    def exhausted_node(state: WorkflowState) -> dict[str, Any]:
        validation = state.get("validation")
        logger.warning(
            "stage=repair_exhausted attempts=%d code=%s",
            state.get("repair_attempts", 0),
            validation.code if validation else None,
        )
        return {
            "answer": FinalAnswer(answer="无法生成可执行的 SQL，已达到最大修复次数。"),
            "error": validation.message if validation else "unknown error",
            "stage": "final",
        }

    def empty_result_node(state: WorkflowState) -> dict[str, Any]:
        logger.info("stage=summarize skipped reason=empty_result")
        answer = FinalAnswer(
            answer="未查询到符合条件的数据。",
            caveats=["SQL 执行成功，但结果集为空。"],
        )
        logger.info(
            "stage=final answer=%s evidence=%d caveats=%d",
            compact_text(answer.answer, 500),
            len(answer.evidence),
            len(answer.caveats),
        )
        return {"answer": answer, "stage": "final"}

    async def summarize_node(state: WorkflowState) -> dict[str, Any]:
        final = await _summarize_with_fallback(
            state["question"],
            state["sql"],
            state["execution"],
        )
        logger.info(
            "stage=final answer=%s evidence=%d caveats=%d",
            compact_text(final.answer, 500),
            len(final.evidence),
            len(final.caveats),
        )
        return {"answer": final, "stage": "final"}

    graph = StateGraph(WorkflowState)
    graph.add_node("select_tables", select_tables_node)
    graph.add_node("check_selection", check_selection_node)
    graph.add_node("generate_sql", generate_sql_node)
    graph.add_node("validate_sql", validate_sql_node)
    graph.add_node("execute_sql", execute_sql_node)
    graph.add_node("rewrite_sql", rewrite_sql_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("empty_result", empty_result_node)
    graph.add_node("exhausted", exhausted_node)

    graph.set_entry_point("select_tables")
    graph.add_edge("select_tables", "check_selection")
    graph.add_conditional_edges(
        "check_selection",
        route_after_selection,
        {"end": END, "retry": "select_tables", "generate": "generate_sql"},
    )
    graph.add_edge("generate_sql", "validate_sql")
    graph.add_conditional_edges(
        "validate_sql",
        route_after_validate,
        {"execute": "execute_sql", "rewrite": "rewrite_sql", "exhausted": "exhausted"},
    )
    graph.add_conditional_edges(
        "execute_sql",
        route_after_execute,
        {
            "summarize": "summarize",
            "empty": "empty_result",
            "rewrite": "rewrite_sql",
            "exhausted": "exhausted",
        },
    )
    graph.add_conditional_edges(
        "rewrite_sql",
        route_after_rewrite,
        {"validate": "validate_sql", "end": END},
    )
    graph.add_edge("summarize", END)
    graph.add_edge("empty_result", END)
    graph.add_edge("exhausted", END)

    return graph.compile()
