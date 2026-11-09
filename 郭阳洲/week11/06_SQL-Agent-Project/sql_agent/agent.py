from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .budget import Budget, BudgetExceeded
from .catalog import DBCatalog, SelectionResult
from .config import Settings, settings as default_settings
from .contracts import (
    ResultContract,
    build_contract,
    check_sanity,
    classify_empty,
    fill_time_series,
)
from .errors import REDLINE_CODES, error_dict, user_message
from .executor import ExecutionResult, SQLExecutor
from .generator import SQLGenerationError, SQLGenerator
from .intent import IntentGate, IntentResult, meta_answer
from .llm import LLM, LLMError
from .privacy import sanitize_log_text
from .rewriter import RewriteNoopError, SQLRewriter
from .selector import TableSelector
from .semantic import (
    MetricAmbiguousError,
    MetricUndefinedError,
    SemanticCatalog,
    SemanticResolution,
)
from .summarizer import ResultSummarizer
from .trace_store import (
    JsonlTraceStore,
    NullTraceStore,
    TraceStore,
    make_trace_id,
    normalize_user_code,
    now_iso,
)
from .validator import SQLValidator

MAX_QUESTION_CHARS = 500


@dataclass
class TraceStep:
    name: str
    status: str
    detail: str = ""
    elapsed_ms: float = 0.0


@dataclass
class AgentResult:
    question: str
    success: bool
    status: str = "error"
    answer: str = ""
    sql: str = ""
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    attempts: int = 0
    validated: bool = False
    executed: bool = False
    error: str = ""
    error_code: str = ""
    error_name: str = ""
    error_class: int = 0
    user_message: str = ""
    suggestions: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    metric: dict[str, Any] | None = None
    time_range: dict[str, Any] | None = None
    data_until: str = ""
    intent: dict[str, Any] | None = None
    selection: dict[str, Any] | None = None
    empty_reason: str | None = None
    masked_columns: list[str] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    grounding_ok: bool = True
    used_fallback: bool = False
    trace_id: str = ""
    user_code: str = "local"
    steps: list[TraceStep] = field(default_factory=list)
    total_ms: float = 0.0
    budget: dict[str, Any] | None = None
    model: str = ""
    app_version: str = ""
    prompt_version: str = ""
    started_at: str = ""
    contract: ResultContract | None = None

    def to_dataframe(self):
        import pandas as pd

        return pd.DataFrame(self.rows, columns=self.columns)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["rows"] = [list(row) for row in self.rows]
        data["contract"] = self.contract.to_dict() if self.contract else None
        return data


class SQLAgent:
    """解析 -> 意图 -> 语义 -> 选表 -> 生成 -> 校验/改写 -> 执行 -> 汇总 -> 审计。"""

    def __init__(
        self,
        catalog: DBCatalog | None = None,
        llm: LLM | None = None,
        settings: Settings = default_settings,
        *,
        semantic: SemanticCatalog | None = None,
        trace_store: TraceStore | None = None,
    ):
        self.settings = settings
        self.catalog = catalog or DBCatalog(settings.db_path, settings.cache_dir).load()
        self.llm = llm or LLM(
            settings.api_key,
            settings.base_url,
            settings.model,
            timeout=settings.llm_timeout_cap_s,
            price_input_cny_per_m=settings.price_input_cny_per_m,
            price_output_cny_per_m=settings.price_output_cny_per_m,
        )
        self.semantic = semantic or SemanticCatalog.load(settings.semantic_dir)
        self.intent_gate = IntentGate(self.semantic)
        self.selector = TableSelector(
            self.catalog, self.llm, max_tables=settings.max_join_tables
        )
        self.generator = SQLGenerator(self.llm)
        self.validator = SQLValidator(
            self.catalog,
            max_sql_chars=settings.max_sql_chars,
            max_tables=settings.max_join_tables,
        )
        self.rewriter = SQLRewriter(self.llm)
        self.executor = SQLExecutor(
            settings.db_path,
            max_rows=settings.max_rows,
            timeout_s=settings.query_timeout_s,
        )
        self.summarizer = ResultSummarizer(self.llm)
        self.trace_store = trace_store or JsonlTraceStore(
            settings.trace_dir, retention_days=settings.retention_days
        )
        self.data_until = self.catalog.data_until or self.semantic.data_until

    # ---------- public ----------

    def run(self, question: str, user_code: str | None = None) -> AgentResult:
        started_at = now_iso()
        start = time.perf_counter()
        trace_id = make_trace_id()
        try:
            user_code = normalize_user_code(user_code)
        except ValueError:
            user_code = "local"
        steps: list[TraceStep] = []
        budget = Budget(
            deadline_s=self.settings.deadline_s,
            max_llm_calls=self.settings.max_llm_calls,
            max_total_tokens=self.settings.max_total_tokens,
            max_cost_cny=self.settings.max_cost_cny,
            min_remaining_s=self.settings.min_budget_s,
        )

        result = AgentResult(
            question=question,
            success=False,
            trace_id=trace_id,
            user_code=user_code,
            model=getattr(self.llm, "model", ""),
            app_version=self.settings.app_version,
            prompt_version=self.settings.prompt_version,
            started_at=started_at,
            data_until=self.data_until,
        )

        # ① 输入与意图
        question = (question or "").strip()
        result.question = question
        t0 = time.perf_counter()
        if not question:
            steps.append(_step("① 输入校验", "error", "问题为空", t0))
            return self._finish(result, steps, start, "E1001", budget, trace_id)
        if len(question) > MAX_QUESTION_CHARS:
            steps.append(_step("① 输入校验", "error", "问题过长", t0))
            return self._finish(result, steps, start, "E1002", budget, trace_id)

        try:
            intent = self.intent_gate.classify(
                question,
                llm=self.llm,
                budget=budget,
                use_llm=self.settings.intent_llm_fallback,
            )
        except (BudgetExceeded, LLMError):
            intent = self.intent_gate.rule_classify(question)
        result.intent = intent.to_dict()
        if intent.refused:
            steps.append(
                _step("① 意图门禁", "refused", f"{intent.intent}: {intent.message}", t0)
            )
            result.answer = (
                meta_answer(self.catalog, question)
                if intent.intent == "meta"
                else intent.message
            )
            result.suggestions = list(intent.suggestions)
            result.status = "error"
            result.error = intent.message
            result.error_code = intent.code
            result.user_message = intent.message
            steps.append(_step("②-⑦", "skipped", "意图不进入 SQL 流程", t0))
            return self._finish(
                result, steps, start, intent.code, budget, trace_id
            )
        steps.append(
            _step("① 意图门禁", "ok", f"intent={intent.intent}", t0)
        )

        # ② 语义预检：未定义指标不浪费 LLM 调用
        t0 = time.perf_counter()
        try:
            self.semantic.bind_metric(question)
        except MetricUndefinedError as exc:
            result.answer = _metric_undefined_answer(exc)
            result.suggestions = [f"{alt}是多少？" for alt in exc.alternatives][:2]
            result.error_code = exc.code
            result.error = exc.detail
            result.user_message = result.answer
            steps.append(_step("② 语义绑定", "refused", exc.detail, t0))
            return self._finish(result, steps, start, exc.code, budget, trace_id)
        except MetricAmbiguousError as exc:
            result.error_code = exc.code
            result.error = exc.detail
            result.user_message = exc.detail
            result.answer = exc.detail
            steps.append(_step("② 语义绑定", "error", exc.detail, t0))
            return self._finish(result, steps, start, exc.code, budget, trace_id)
        steps.append(_step("② 语义绑定", "ok", "指标/时间预检通过", t0))

        # ③ 选表
        t0 = time.perf_counter()
        selection: SelectionResult
        try:
            selection = self.selector.select(question, budget=budget, semantic=self.semantic)
            detail = f"选中表: {', '.join(selection.tables)}\n理由: {selection.reason}"
            if selection.used_fallback:
                detail += "\n（使用了兜底策略）"
            steps.append(_step("③ 表选择", "ok", detail, t0))
        except (BudgetExceeded, LLMError) as exc:
            selection = self.selector._fallback(
                self.selector.hint_tables(question),
                f"选表失败: {exc}",
                [{"code": getattr(exc, "code", "E3001"), "detail": str(exc)}],
            )
            steps.append(_step("③ 表选择", "fallback", str(exc), t0))
        except Exception as exc:  # noqa: BLE001
            selection = SelectionResult(
                tables=list(self.catalog.table_names),
                reason=f"选表异常，回退全表: {exc}",
                used_fallback=True,
                confidence="low",
                warnings=[{"code": "E3001", "detail": str(exc)}],
                fallback_all=True,
            )
            steps.append(_step("③ 表选择", "fallback", str(exc), t0))

        result.tables = list(selection.tables)
        result.selection = selection.to_dict()
        warnings: list[dict[str, Any]] = list(selection.warnings)

        # ④ 语义解析（结合选中的表决定 revenue_line 等变体）
        t0 = time.perf_counter()
        try:
            resolution = self.semantic.resolve(question, selection.tables)
        except (MetricUndefinedError, MetricAmbiguousError) as exc:
            result.answer = (
                _metric_undefined_answer(exc)
                if isinstance(exc, MetricUndefinedError)
                else exc.detail
            )
            result.suggestions = (
                [f"{alt}是多少？" for alt in exc.alternatives][:2]
                if isinstance(exc, MetricUndefinedError)
                else []
            )
            result.error_code = exc.code
            result.error = exc.detail
            result.user_message = result.answer
            steps.append(_step("④ 语义解析", "refused", exc.detail, t0))
            return self._finish(result, steps, start, exc.code, budget, trace_id)
        result.metric = resolution.metric.to_dict() if resolution.metric else None
        result.time_range = resolution.time_range.to_dict()
        result.assumptions = list(resolution.assumptions)
        warnings.extend(warning.to_dict() for warning in resolution.warnings)
        steps.append(
            _step(
                "④ 语义解析",
                "ok",
                f"metric={resolution.metric_id or 'none'}; time={resolution.time_range.label}",
                t0,
            )
        )

        schema = self.catalog.schema_text(selection.tables)

        # ⑤ 生成
        sql = ""
        execution: ExecutionResult | None = None
        t0 = time.perf_counter()
        try:
            sql = self.generator.generate(
                question,
                schema,
                semantic=resolution,
                budget=budget,
                data_until=self.data_until,
            )
            steps.append(_step("⑤ SQL 生成", "ok", sql, t0))
        except (SQLGenerationError, BudgetExceeded) as exc:
            code = getattr(exc, "code", "E4002")
            steps.append(_step("⑤ SQL 生成", "error", str(exc), t0))
            if code == "E4003" and resolution.time_range.out_of_range:
                # 时间范围超出数据截止时，生成器可能直接拒答；按空结果归因处理
                steps.append(
                    _step("⑤ SQL 生成", "empty", "时间范围超出数据，按空结果处理", t0)
                )
                execution = ExecutionResult(ok=True)
            else:
                result.error = str(exc)
                result.error_code = code
                result.user_message = user_message(code)
                result.answer = result.user_message
                return self._finish(result, steps, start, code, budget, trace_id)

        # ⑥ 校验 / 改写 / 执行
        history: list[dict[str, Any]] = []
        attempts = 0
        last_code = ""
        last_error = ""

        attempts_range = (
            range(1, self.settings.max_rewrites + 2) if execution is None else ()
        )
        for attempt in attempts_range:
            attempts = attempt
            t0 = time.perf_counter()
            validation = self.validator.validate(sql)
            if not validation.ok:
                last_code, last_error = validation.code, validation.error
                error = f"{validation.code} {validation.stage}: {validation.error}"
                steps.append(_step("⑥ SQL 校验", "error", f"{error}\n{sql}", t0))
                history.append({"attempt": attempt, "sql": sql, "error": error})
                if validation.code in REDLINE_CODES:
                    result.answer = (
                        f"{user_message(validation.code)}（{validation.error}）"
                    )
                    steps.append(_step("⑥ 改写", "blocked", "安全红线，不重试", t0))
                    break
                if attempt > self.settings.max_rewrites:
                    break
                if not budget.can_call():
                    steps.append(_step("⑥ 改写", "blocked", "预算不足，停止改写", t0))
                    break
                t0 = time.perf_counter()
                try:
                    sql = self.rewriter.rewrite(
                        question,
                        schema,
                        sql,
                        error,
                        history,
                        semantic=resolution,
                        budget=budget,
                        data_until=self.data_until,
                    )
                    steps.append(_step("⑥ SQL 改写", "ok", sql, t0))
                except RewriteNoopError as exc:
                    last_code, last_error = exc.code, str(exc)
                    steps.append(_step("⑥ SQL 改写", "blocked", str(exc), t0))
                    break
                except (SQLGenerationError, BudgetExceeded) as exc:
                    last_code = getattr(exc, "code", "E4002")
                    last_error = str(exc)
                    steps.append(_step("⑥ SQL 改写", "error", str(exc), t0))
                    break
                continue

            result.validated = True
            steps.append(_step("⑥ SQL 校验", "ok", validation.error or "校验通过", t0))

            t0 = time.perf_counter()
            execution = self.executor.execute(sql, budget=budget)
            if not execution.ok:
                last_code = execution.error_code or "E6002"
                last_error = execution.error
                error = f"{last_code}: {execution.error}"
                steps.append(_step("⑦ SQL 执行", "error", error, t0))
                history.append({"attempt": attempt, "sql": sql, "error": error})
                if last_code == "E5010":
                    break
                if attempt > self.settings.max_rewrites or not budget.can_call():
                    break
                t0 = time.perf_counter()
                try:
                    sql = self.rewriter.rewrite(
                        question,
                        schema,
                        sql,
                        error,
                        history,
                        semantic=resolution,
                        budget=budget,
                        data_until=self.data_until,
                    )
                    steps.append(_step("⑥ SQL 改写", "ok", sql, t0))
                except RewriteNoopError as exc:
                    last_code, last_error = exc.code, str(exc)
                    steps.append(_step("⑥ SQL 改写", "blocked", str(exc), t0))
                    break
                except (SQLGenerationError, BudgetExceeded) as exc:
                    last_code = getattr(exc, "code", "E4002")
                    last_error = str(exc)
                    steps.append(_step("⑥ SQL 改写", "error", str(exc), t0))
                    break
                continue

            detail = f"返回 {execution.row_count} 行，耗时 {execution.elapsed_ms:.1f} ms"
            if execution.truncated:
                detail += f"（已截断到 {self.settings.max_rows} 行）"
            if execution.masked_columns:
                detail += f"；已脱敏列: {', '.join(execution.masked_columns)}"
            steps.append(_step("⑦ SQL 执行", "ok", detail, t0))
            break

        result.attempts = attempts
        result.sql = sql

        if execution is None or not execution.ok:
            code = last_code or "E6002"
            result.status = "error"
            result.error = last_error
            result.error_code = code
            result.user_message = user_message(code)
            if not result.answer:
                result.answer = result.user_message
            result.executed = False
            return self._finish(result, steps, start, code, budget, trace_id)

        result.executed = True
        result.rows = list(execution.rows)
        result.columns = list(execution.columns)
        result.truncated = execution.truncated
        result.masked_columns = list(execution.masked_columns)

        # ⑧ 空结果归因
        empty = classify_empty(
            execution.columns,
            execution.rows,
            out_of_range=resolution.time_range.out_of_range,
            data_until=self.data_until,
            probe=lambda: self._probe(selection.tables),
        )
        if empty is not None:
            warnings.append(
                {"code": empty.code, "detail": empty.message, "severity": "info"}
            )

        # ⑨ sanity + 时间补零
        base_rows = self.catalog.base_row_count(selection.tables)
        for issue in check_sanity(
            execution.columns,
            execution.rows,
            base_row_count=base_rows,
            truncated=execution.truncated,
        ):
            warnings.append(issue.to_dict())

        if empty is None and resolution.time_range.granularity == "month":
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
                result.rows = list(filled.rows)
                result.row_count = execution.row_count
                result.assumptions.append(filled.assumption)
                warnings.append(
                    {"code": "E7006", "detail": filled.assumption, "severity": "info"}
                )

        result.row_count = execution.row_count
        result.empty_reason = empty.reason if empty else None

        # ⑩ 汇总 + grounding
        t0 = time.perf_counter()
        summary = self.summarizer.summarize(
            question,
            sql,
            execution,
            metric=result.metric,
            time_range=result.time_range,
            data_until=self.data_until,
            assumptions=result.assumptions,
            empty=empty,
            budget=budget,
        )
        result.answer = summary.answer
        result.claims = [claim.to_dict() for claim in summary.claims]
        result.grounding_ok = summary.grounding_ok
        result.used_fallback = summary.used_fallback
        warnings.extend(summary.warnings)
        steps.append(
            _step(
                "⑨ 结果汇总",
                "ok" if not summary.used_fallback else "fallback",
                f"{summary.answer[:120]}",
                t0,
            )
        )

        contract = build_contract(
            execution,
            metric=result.metric,
            time_range=result.time_range,
            data_until=self.data_until,
            warnings=warnings,
            trace_id=trace_id,
            empty=empty,
            error_code="",
        )
        result.contract = contract
        result.status = contract.status
        result.success = contract.status in {"ok", "empty", "truncated"}
        result.warnings = warnings
        return self._finish(
            result,
            steps,
            start,
            "" if result.success else (summary.error_code or "E8005"),
            budget,
            trace_id,
        )

    # ---------- helpers ----------

    def _probe(self, tables: list[str]) -> bool:
        for table in tables:
            if not self.catalog.has_table(table):
                continue
            result = self.executor.execute(f'SELECT 1 FROM "{table}" LIMIT 1')
            if result.ok and result.rows:
                return True
        return False

    def _finish(
        self,
        result: AgentResult,
        steps: list[TraceStep],
        start: float,
        code: str,
        budget: Budget,
        trace_id: str,
    ) -> AgentResult:
        result.steps = steps
        result.total_ms = (time.perf_counter() - start) * 1000
        result.budget = asdict(budget.snapshot())
        result.trace_id = trace_id

        if code:
            info = error_dict(code, result.error)
            result.error_code = code
            result.error_name = info["name"]
            result.error_class = info["error_class"]
            if not result.user_message:
                result.user_message = info["message"]
            if not result.answer:
                result.answer = info["message"]
        elif result.error_code:
            info = error_dict(result.error_code, result.error)
            result.error_name = info["name"]
            result.error_class = info["error_class"]

        if result.contract is None:
            result.contract = ResultContract(
                status=result.status,
                columns=list(result.columns),
                rows=list(result.rows),
                row_count=result.row_count,
                truncated=result.truncated,
                metric=result.metric,
                time_range=result.time_range,
                data_until=self.data_until,
                warnings=list(result.warnings),
                trace_id=trace_id,
                empty_reason=result.empty_reason,
                error_code=result.error_code,
            )

        event = {
            "trace_id": trace_id,
            "user_code": result.user_code,
            "timestamp": result.started_at,
            "question": sanitize_log_text(result.question)[:500],
            "answer": sanitize_log_text(result.answer)[:1500],
            "sql": sanitize_log_text(result.sql)[:4000],
            "status": result.status,
            "success": result.success,
            "tables": result.tables,
            "metric": (result.metric or {}).get("id"),
            "time_range": result.time_range,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "attempts": result.attempts,
            "error_code": result.error_code,
            "error_class": result.error_class,
            "warnings": result.warnings,
            "total_ms": round(result.total_ms, 1),
            "budget": result.budget,
            "model": result.model,
            "app_version": result.app_version,
            "prompt_version": result.prompt_version,
            "catalog_fingerprint": self.catalog.fingerprint,
        }
        try:
            written = self.trace_store.write(event)
        except Exception as exc:  # noqa: BLE001
            written = False
            result.warnings.append({"code": "E8009", "detail": str(exc)})
        if not written and not isinstance(self.trace_store, NullTraceStore):
            result.warnings.append(
                {"code": "E8009", "detail": "审计写入失败，本次回答仍有效"}
            )
            if result.contract is not None:
                result.contract.warnings = list(result.warnings)
        return result


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
