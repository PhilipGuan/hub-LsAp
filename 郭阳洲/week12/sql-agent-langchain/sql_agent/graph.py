"""LangGraph 状态机：把原 SQLAgent.run() 的手写流程平移为图编排。

节点与原流程一一对应：
  ① input_check 输入检查 → ① intent_gate 意图门禁 → ② semantic_bind 语义预检
  → ③ select_tables 选表 → ④ semantic_resolve 语义解析 → ⑤ generate_sql 生成
  → ⑥ validate_sql / rewrite_sql（改写环，≤ max_rewrites）
  → ⑦ execute_sql 只读执行 → ⑧ classify_empty 空结果归因
  → ⑨ sanity_fill 合理性检查与月度补零 → ⑩ summarize 受约束汇总
  → finalize 错误码/契约归一 → END

依赖注入：build_graph(deps) 为闭包工厂，GraphDeps 携带全部组件与请求级
Budget/trace_id；每次请求新建图实例，state 保持纯数据。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from .budget import Budget, BudgetExceeded
from .catalog import DBCatalog, SelectionResult
from .config import Settings
from .contracts import (
    EmptyClassification,
    ResultContract,
    build_contract,
    check_sanity,
    classify_empty as classify_empty_result,
    fill_time_series,
)
from .errors import REDLINE_CODES, error_dict, user_message
from .executor import ExecutionResult, SQLExecutor
from .generator import SQLGenerationError, SQLGenerator
from .intent import IntentGate, meta_answer
from .llm import LLM, LLMError
from .rewriter import RewriteNoopError, SQLRewriter
from .selector import TableSelector
from .semantic import (
    MetricAmbiguousError,
    MetricUndefinedError,
    SemanticCatalog,
    SemanticResolution,
)
from .state import AgentState, TraceStep
from .summarizer import ResultSummarizer
from .validator import SQLValidator

MAX_QUESTION_CHARS = 500


def _step(name: str, status: str, detail: str, t0: float) -> TraceStep:
    return TraceStep(
        name=name,
        status=status,
        detail=detail,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def _metric_undefined_answer(exc: MetricUndefinedError) -> str:
    text = f"当前未定义指标「{exc.mention}」，因此不能生成该口径的 SQL。"
    if exc.alternatives:
        text += "可以改用：" + "、".join(exc.alternatives) + "。"
    return text


@dataclass
class GraphDeps:
    """图运行期依赖（组件 + 请求级对象）。"""

    settings: Settings
    catalog: DBCatalog
    semantic: SemanticCatalog
    llm: LLM
    intent_gate: IntentGate
    selector: TableSelector
    generator: SQLGenerator
    validator: SQLValidator
    rewriter: SQLRewriter
    executor: SQLExecutor
    summarizer: ResultSummarizer
    budget: Budget
    data_until: str


def _probe(deps: GraphDeps, tables: list[str]) -> bool:
    for table in tables:
        if not deps.catalog.has_table(table):
            continue
        result = deps.executor.execute(f'SELECT 1 FROM "{table}" LIMIT 1')
        if result.ok and result.rows:
            return True
    return False


def build_graph(deps: GraphDeps):
    """编译本次请求的状态图。"""

    # ---------- 节点 ----------

    def input_check(state: AgentState) -> dict:
        question = (state.get("question") or "").strip()
        t0 = time.perf_counter()
        if not question:
            return {
                "question": question,
                "steps": [_step("① 输入校验", "error", "问题为空", t0)],
                "error_code": "E1001",
                "route": "finalize",
            }
        if len(question) > MAX_QUESTION_CHARS:
            return {
                "question": question,
                "steps": [_step("① 输入校验", "error", "问题过长", t0)],
                "error_code": "E1002",
                "route": "finalize",
            }
        return {"question": question, "route": "intent_gate"}

    def intent_gate(state: AgentState) -> dict:
        question = state["question"]
        t0 = time.perf_counter()
        try:
            intent = deps.intent_gate.classify(
                question,
                llm=deps.llm,
                budget=deps.budget,
                use_llm=deps.settings.intent_llm_fallback,
            )
        except (BudgetExceeded, LLMError):
            intent = deps.intent_gate.rule_classify(question)

        updates: dict[str, Any] = {"intent": intent.to_dict()}
        if intent.refused:
            updates["steps"] = [
                _step(
                    "① 意图门禁",
                    "refused",
                    f"{intent.intent}: {intent.message}",
                    t0,
                ),
                _step("②-⑦", "skipped", "意图不进入 SQL 流程", t0),
            ]
            updates["answer"] = (
                meta_answer(deps.catalog, question)
                if intent.intent == "meta"
                else intent.message
            )
            updates["suggestions"] = list(intent.suggestions)
            updates["status"] = "error"
            updates["error"] = intent.message
            updates["error_code"] = intent.code
            updates["user_message"] = intent.message
            updates["route"] = "finalize"
        else:
            updates["steps"] = [
                _step("① 意图门禁", "ok", f"intent={intent.intent}", t0)
            ]
            updates["route"] = "semantic_bind"
        return updates

    def semantic_bind(state: AgentState) -> dict:
        question = state["question"]
        t0 = time.perf_counter()
        try:
            deps.semantic.bind_metric(question)
        except MetricUndefinedError as exc:
            answer = _metric_undefined_answer(exc)
            return {
                "steps": [_step("② 语义绑定", "refused", exc.detail, t0)],
                "answer": answer,
                "suggestions": [f"{alt}是多少？" for alt in exc.alternatives][:2],
                "error_code": exc.code,
                "error": exc.detail,
                "user_message": answer,
                "route": "finalize",
            }
        except MetricAmbiguousError as exc:
            return {
                "steps": [_step("② 语义绑定", "error", exc.detail, t0)],
                "error_code": exc.code,
                "error": exc.detail,
                "user_message": exc.detail,
                "answer": exc.detail,
                "route": "finalize",
            }
        return {
            "steps": [_step("② 语义绑定", "ok", "指标/时间预检通过", t0)],
            "route": "select_tables",
        }

    def select_tables(state: AgentState) -> dict:
        question = state["question"]
        t0 = time.perf_counter()
        try:
            selection = deps.selector.select(
                question, budget=deps.budget, semantic=deps.semantic
            )
            detail = f"选中表: {', '.join(selection.tables)}\n理由: {selection.reason}"
            if selection.used_fallback:
                detail += "\n（使用了兜底策略）"
            steps = [_step("③ 表选择", "ok", detail, t0)]
        except (BudgetExceeded, LLMError) as exc:
            selection = deps.selector._fallback(
                deps.selector.hint_tables(question),
                f"选表失败: {exc}",
                [{"code": getattr(exc, "code", "E3001"), "detail": str(exc)}],
            )
            steps = [_step("③ 表选择", "fallback", str(exc), t0)]
        except Exception as exc:  # noqa: BLE001
            selection = SelectionResult(
                tables=list(deps.catalog.table_names),
                reason=f"选表异常，回退全表: {exc}",
                used_fallback=True,
                confidence="low",
                warnings=[{"code": "E3001", "detail": str(exc)}],
                fallback_all=True,
            )
            steps = [_step("③ 表选择", "fallback", str(exc), t0)]
        return {
            "steps": steps,
            "tables": list(selection.tables),
            "selection": selection,
            "warnings": list(selection.warnings),
            "route": "semantic_resolve",
        }

    def semantic_resolve(state: AgentState) -> dict:
        question = state["question"]
        selection = state["selection"]
        t0 = time.perf_counter()
        try:
            resolution: SemanticResolution = deps.semantic.resolve(
                question, selection.tables
            )
        except MetricUndefinedError as exc:
            answer = _metric_undefined_answer(exc)
            return {
                "steps": [_step("④ 语义解析", "refused", exc.detail, t0)],
                "answer": answer,
                "suggestions": [f"{alt}是多少？" for alt in exc.alternatives][:2],
                "error_code": exc.code,
                "error": exc.detail,
                "user_message": answer,
                "route": "finalize",
            }
        except MetricAmbiguousError as exc:
            return {
                "steps": [_step("④ 语义解析", "refused", exc.detail, t0)],
                "error_code": exc.code,
                "error": exc.detail,
                "user_message": exc.detail,
                "answer": exc.detail,
                "route": "finalize",
            }
        return {
            "steps": [
                _step(
                    "④ 语义解析",
                    "ok",
                    f"metric={resolution.metric_id or 'none'}; "
                    f"time={resolution.time_range.label}",
                    t0,
                )
            ],
            "resolution": resolution,
            "metric": resolution.metric.to_dict() if resolution.metric else None,
            "time_range": resolution.time_range.to_dict(),
            "assumptions": list(resolution.assumptions),
            "warnings": [warning.to_dict() for warning in resolution.warnings],
            "schema_text": deps.catalog.schema_text(selection.tables),
            "route": "generate_sql",
        }

    def generate_sql(state: AgentState) -> dict:
        question = state["question"]
        resolution = state.get("resolution")
        t0 = time.perf_counter()
        try:
            sql = deps.generator.generate(
                question,
                state.get("schema_text", ""),
                semantic=resolution,
                budget=deps.budget,
                data_until=deps.data_until,
            )
            return {
                "steps": [_step("⑤ SQL 生成", "ok", sql, t0)],
                "sql": sql,
                "route": "validate_sql",
            }
        except (SQLGenerationError, BudgetExceeded) as exc:
            code = getattr(exc, "code", "E4002")
            steps = [_step("⑤ SQL 生成", "error", str(exc), t0)]
            if code == "E4003" and resolution is not None and resolution.time_range.out_of_range:
                # 时间范围超出数据截止时，生成器可能直接拒答；按空结果归因处理
                steps.append(
                    _step("⑤ SQL 生成", "empty", "时间范围超出数据，按空结果处理", t0)
                )
                return {
                    "steps": steps,
                    "execution": ExecutionResult(ok=True),
                    "executed": True,
                    "route": "classify_empty",
                }
            message = user_message(code)
            return {
                "steps": steps,
                "error": str(exc),
                "error_code": code,
                "user_message": message,
                "answer": message,
                "route": "finalize",
            }

    def validate_sql(state: AgentState) -> dict:
        sql = state["sql"]
        attempts = state.get("attempts", 0) + 1
        t0 = time.perf_counter()
        validation = deps.validator.validate(sql)
        if validation.ok:
            return {
                "attempts": attempts,
                "validated": True,
                "steps": [
                    _step("⑥ SQL 校验", "ok", validation.error or "校验通过", t0)
                ],
                "route": "execute_sql",
            }

        error = f"{validation.code} {validation.stage}: {validation.error}"
        base: dict[str, Any] = {
            "attempts": attempts,
            "history": [{"attempt": attempts, "sql": sql, "error": error}],
            "rewrite_error": error,
            "error_code": validation.code,
            "error": validation.error,
            "route": "finalize",
        }
        if validation.code in REDLINE_CODES:
            base["steps"] = [
                _step("⑥ SQL 校验", "error", f"{error}\n{sql}", t0),
                _step("⑥ 改写", "blocked", "安全红线，不重试", t0),
            ]
            base["answer"] = (
                f"{user_message(validation.code)}（{validation.error}）"
            )
            return base
        if attempts > deps.settings.max_rewrites:
            base["steps"] = [_step("⑥ SQL 校验", "error", f"{error}\n{sql}", t0)]
            return base
        if not deps.budget.can_call():
            base["steps"] = [
                _step("⑥ SQL 校验", "error", f"{error}\n{sql}", t0),
                _step("⑥ 改写", "blocked", "预算不足，停止改写", t0),
            ]
            return base
        base["steps"] = [_step("⑥ SQL 校验", "error", f"{error}\n{sql}", t0)]
        base["route"] = "rewrite_sql"
        return base

    def rewrite_sql(state: AgentState) -> dict:
        t0 = time.perf_counter()
        try:
            sql = deps.rewriter.rewrite(
                state["question"],
                state.get("schema_text", ""),
                state["sql"],
                state.get("rewrite_error", ""),
                state.get("history") or [],
                semantic=state.get("resolution"),
                budget=deps.budget,
                data_until=deps.data_until,
            )
            return {
                "sql": sql,
                "steps": [_step("⑥ SQL 改写", "ok", sql, t0)],
                "route": "validate_sql",
            }
        except RewriteNoopError as exc:
            return {
                "steps": [_step("⑥ SQL 改写", "blocked", str(exc), t0)],
                "error_code": exc.code,
                "error": str(exc),
                "route": "finalize",
            }
        except (SQLGenerationError, BudgetExceeded) as exc:
            return {
                "steps": [_step("⑥ SQL 改写", "error", str(exc), t0)],
                "error_code": getattr(exc, "code", "E4002"),
                "error": str(exc),
                "route": "finalize",
            }

    def execute_sql(state: AgentState) -> dict:
        sql = state["sql"]
        attempts = state.get("attempts", 0)
        t0 = time.perf_counter()
        execution = deps.executor.execute(sql, budget=deps.budget)
        if execution.ok:
            detail = f"返回 {execution.row_count} 行，耗时 {execution.elapsed_ms:.1f} ms"
            if execution.truncated:
                detail += f"（已截断到 {deps.settings.max_rows} 行）"
            if execution.masked_columns:
                detail += f"；已脱敏列: {', '.join(execution.masked_columns)}"
            return {
                "steps": [_step("⑦ SQL 执行", "ok", detail, t0)],
                "execution": execution,
                "executed": True,
                "columns": list(execution.columns),
                "rows": list(execution.rows),
                "row_count": execution.row_count,
                "truncated": execution.truncated,
                "masked_columns": list(execution.masked_columns),
                "route": "classify_empty",
            }

        last_code = execution.error_code or "E6002"
        last_error = execution.error
        error = f"{last_code}: {execution.error}"
        base: dict[str, Any] = {
            "steps": [_step("⑦ SQL 执行", "error", error, t0)],
            "history": [{"attempt": attempts, "sql": sql, "error": error}],
            "rewrite_error": error,
            "error_code": last_code,
            "error": last_error,
            "route": "finalize",
        }
        if last_code == "E5010":
            return base
        if attempts > deps.settings.max_rewrites or not deps.budget.can_call():
            return base
        base["route"] = "rewrite_sql"
        return base

    def classify_empty(state: AgentState) -> dict:
        execution = state["execution"]
        resolution = state.get("resolution")
        selection = state.get("selection")
        tables = list(selection.tables) if selection is not None else []
        empty: EmptyClassification | None = classify_empty_result(
            execution.columns,
            execution.rows,
            out_of_range=(
                resolution.time_range.out_of_range
                if resolution is not None
                else False
            ),
            data_until=deps.data_until,
            probe=lambda: _probe(deps, tables),
        )
        updates: dict[str, Any] = {
            "empty": empty,
            "empty_reason": empty.reason if empty else None,
            "route": "sanity_fill",
        }
        if empty is not None:
            updates["warnings"] = [
                {"code": empty.code, "detail": empty.message, "severity": "info"}
            ]
        return updates

    def sanity_fill(state: AgentState) -> dict:
        execution = state["execution"]
        selection = state.get("selection")
        resolution = state.get("resolution")
        empty = state.get("empty")
        base_rows = deps.catalog.base_row_count(
            list(selection.tables) if selection is not None else []
        )
        warnings = [
            issue.to_dict()
            for issue in check_sanity(
                execution.columns,
                execution.rows,
                base_row_count=base_rows,
                truncated=execution.truncated,
            )
        ]
        updates: dict[str, Any] = {"warnings": warnings, "route": "summarize"}
        if (
            empty is None
            and resolution is not None
            and resolution.time_range.granularity == "month"
        ):
            filled = fill_time_series(
                execution.columns,
                execution.rows,
                start=resolution.time_range.start,
                end=resolution.time_range.end,
                granularity=resolution.time_range.granularity,
            )
            if filled.filled:
                execution.rows = list(filled.rows)
                execution.row_count = len(filled.rows)
                updates["execution"] = execution
                updates["rows"] = list(filled.rows)
                updates["row_count"] = execution.row_count
                updates["assumptions"] = [filled.assumption]
                warnings.append(
                    {"code": "E7006", "detail": filled.assumption, "severity": "info"}
                )
        return updates

    def summarize(state: AgentState) -> dict:
        execution = state["execution"]
        t0 = time.perf_counter()
        summary = deps.summarizer.summarize(
            state["question"],
            state.get("sql", ""),
            execution,
            metric=state.get("metric"),
            time_range=state.get("time_range"),
            data_until=deps.data_until,
            assumptions=list(state.get("assumptions") or []),
            empty=state.get("empty"),
            budget=deps.budget,
        )
        return {
            "steps": [
                _step(
                    "⑨ 结果汇总",
                    "ok" if not summary.used_fallback else "fallback",
                    summary.answer[:120],
                    t0,
                )
            ],
            "answer": summary.answer,
            "claims": [claim.to_dict() for claim in summary.claims],
            "grounding_ok": summary.grounding_ok,
            "used_fallback": summary.used_fallback,
            "warnings": list(summary.warnings),
            "summary": summary,
            "route": "finalize",
        }

    def finalize(state: AgentState) -> dict:
        summary = state.get("summary")
        if summary is not None:
            # ⑩ 成功/空结果/截断路径：构建契约并归一状态
            contract = build_contract(
                state["execution"],
                metric=state.get("metric"),
                time_range=state.get("time_range"),
                data_until=deps.data_until,
                warnings=list(state.get("warnings") or []),
                trace_id=state.get("trace_id", ""),
                empty=state.get("empty"),
                error_code="",
            )
            updates: dict[str, Any] = {
                "contract": contract,
                "status": contract.status,
                "success": contract.status in {"ok", "empty", "truncated"},
            }
            code = "" if updates["success"] else (summary.error_code or "E8005")
            if code:
                info = error_dict(code, state.get("error", ""))
                updates["error_code"] = code
                updates["error_name"] = info["name"]
                updates["error_class"] = info["error_class"]
                if not state.get("user_message"):
                    updates["user_message"] = info["message"]
                if not state.get("answer"):
                    updates["answer"] = info["message"]
            return updates

        # 错误 / 拒答路径
        updates: dict[str, Any] = {
            "status": state.get("status", "error"),
            "success": bool(state.get("success", False)),
        }
        code = state.get("error_code", "")
        if code:
            info = error_dict(code, state.get("error", ""))
            updates["error_code"] = code
            updates["error_name"] = info["name"]
            updates["error_class"] = info["error_class"]
            if not state.get("user_message"):
                updates["user_message"] = info["message"]
            if not state.get("answer"):
                updates["answer"] = info["message"]
        execution = state.get("execution")
        updates["contract"] = ResultContract(
            status=updates["status"],
            columns=list(state.get("columns") or []),
            rows=list(state.get("rows") or []),
            row_count=state.get("row_count", 0),
            truncated=state.get("truncated", False),
            metric=state.get("metric"),
            time_range=state.get("time_range"),
            data_until=deps.data_until,
            warnings=list(state.get("warnings") or []),
            trace_id=state.get("trace_id", ""),
            empty_reason=state.get("empty_reason"),
            error_code=code,
        )
        return updates

    # ---------- 装配 ----------

    builder = StateGraph(AgentState)
    builder.add_node("input_check", input_check)
    builder.add_node("intent_gate", intent_gate)
    builder.add_node("semantic_bind", semantic_bind)
    builder.add_node("select_tables", select_tables)
    builder.add_node("semantic_resolve", semantic_resolve)
    builder.add_node("generate_sql", generate_sql)
    builder.add_node("validate_sql", validate_sql)
    builder.add_node("rewrite_sql", rewrite_sql)
    builder.add_node("execute_sql", execute_sql)
    builder.add_node("classify_empty", classify_empty)
    builder.add_node("sanity_fill", sanity_fill)
    builder.add_node("summarize", summarize)
    builder.add_node("finalize", finalize)

    def route(state: AgentState) -> str:
        return state.get("route", "finalize")

    builder.add_edge(START, "input_check")
    builder.add_conditional_edges("input_check", route)
    builder.add_conditional_edges("intent_gate", route)
    builder.add_conditional_edges("semantic_bind", route)
    builder.add_conditional_edges("select_tables", route)
    builder.add_conditional_edges("semantic_resolve", route)
    builder.add_conditional_edges("generate_sql", route)
    builder.add_conditional_edges("validate_sql", route)
    builder.add_conditional_edges("rewrite_sql", route)
    builder.add_conditional_edges("execute_sql", route)
    builder.add_conditional_edges("classify_empty", route)
    builder.add_conditional_edges("sanity_fill", route)
    builder.add_conditional_edges("summarize", route)
    builder.add_edge("finalize", END)
    return builder.compile()
