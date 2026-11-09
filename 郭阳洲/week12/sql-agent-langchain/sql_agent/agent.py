from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .budget import Budget
from .catalog import DBCatalog
from .config import Settings, settings as default_settings
from .executor import SQLExecutor
from .generator import SQLGenerator
from .graph import GraphDeps, build_graph
from .intent import IntentGate
from .llm import LLM
from .privacy import sanitize_log_text
from .rewriter import SQLRewriter
from .selector import TableSelector
from .semantic import SemanticCatalog
from .state import AgentState, TraceStep  # noqa: F401 - TraceStep 保持从 agent 可导入
from .summarizer import ResultSummarizer
from .trace_store import (
    FeedbackStore,  # noqa: F401 - re-export 便于入口层导入
    JsonlTraceStore,
    NullTraceStore,
    TraceStore,
    make_trace_id,
    normalize_user_code,
    now_iso,
)
from .validator import SQLValidator


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
    contract: Any | None = None

    def to_dataframe(self):
        import pandas as pd

        return pd.DataFrame(self.rows, columns=self.columns)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["rows"] = [list(row) for row in self.rows]
        contract = self.contract
        data["contract"] = (
            contract.to_dict() if hasattr(contract, "to_dict") else contract
        )
        data["steps"] = [
            asdict(step) if isinstance(step, TraceStep) else step
            for step in self.steps
        ]
        return data


class SQLAgent:
    """LangGraph 编排：意图 → 语义 → 选表 → 生成 → 校验/改写 → 执行 → 汇总 → 审计。

    对外契约（run 签名、AgentResult 字段、错误码体系）与原实现完全一致。
    """

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

        budget = Budget(
            deadline_s=self.settings.deadline_s,
            max_llm_calls=self.settings.max_llm_calls,
            max_total_tokens=self.settings.max_total_tokens,
            max_cost_cny=self.settings.max_cost_cny,
            min_remaining_s=self.settings.min_budget_s,
        )

        state: AgentState = {
            "question": question or "",
            "user_code": user_code,
            "trace_id": trace_id,
            "started_at": started_at,
            "data_until": self.data_until,
            "steps": [],
            "warnings": [],
            "assumptions": [],
            "history": [],
            "attempts": 0,
        }
        deps = GraphDeps(
            settings=self.settings,
            catalog=self.catalog,
            semantic=self.semantic,
            llm=self.llm,
            intent_gate=self.intent_gate,
            selector=self.selector,
            generator=self.generator,
            validator=self.validator,
            rewriter=self.rewriter,
            executor=self.executor,
            summarizer=self.summarizer,
            budget=budget,
            data_until=self.data_until,
        )
        graph = build_graph(deps)
        # 改写环兜底：正常最坏路径 ≈ 2*max_rewrites+15 个节点，上限放宽到 +20
        config = {"recursion_limit": 2 * self.settings.max_rewrites + 20}
        final_state = graph.invoke(state, config)
        result = self._to_result(final_state, budget, start)
        return self._write_trace(result)

    # ---------- helpers ----------

    def _to_result(
        self, state: dict[str, Any], budget: Budget, start: float
    ) -> AgentResult:
        selection = state.get("selection")
        if selection is not None and hasattr(selection, "to_dict"):
            selection = selection.to_dict()
        contract = state.get("contract")
        return AgentResult(
            question=state.get("question", ""),
            success=bool(state.get("success", False)),
            status=state.get("status", "error"),
            answer=state.get("answer", ""),
            sql=state.get("sql", ""),
            tables=list(state.get("tables") or []),
            columns=list(state.get("columns") or []),
            rows=[tuple(row) for row in (state.get("rows") or [])],
            row_count=int(state.get("row_count", 0) or 0),
            truncated=bool(state.get("truncated", False)),
            attempts=int(state.get("attempts", 0) or 0),
            validated=bool(state.get("validated", False)),
            executed=bool(state.get("executed", False)),
            error=state.get("error", ""),
            error_code=state.get("error_code", ""),
            error_name=state.get("error_name", ""),
            error_class=int(state.get("error_class", 0) or 0),
            user_message=state.get("user_message", ""),
            suggestions=list(state.get("suggestions") or []),
            warnings=list(state.get("warnings") or []),
            assumptions=list(state.get("assumptions") or []),
            metric=state.get("metric"),
            time_range=state.get("time_range"),
            data_until=state.get("data_until", self.data_until),
            intent=state.get("intent"),
            selection=selection,
            empty_reason=state.get("empty_reason"),
            masked_columns=list(state.get("masked_columns") or []),
            claims=list(state.get("claims") or []),
            grounding_ok=bool(state.get("grounding_ok", True)),
            used_fallback=bool(state.get("used_fallback", False)),
            trace_id=state.get("trace_id", ""),
            user_code=state.get("user_code", "local"),
            steps=list(state.get("steps") or []),
            total_ms=(time.perf_counter() - start) * 1000,
            budget=asdict(budget.snapshot()),
            model=getattr(self.llm, "model", ""),
            app_version=self.settings.app_version,
            prompt_version=self.settings.prompt_version,
            started_at=state.get("started_at", ""),
            contract=contract,
        )

    def _write_trace(self, result: AgentResult) -> AgentResult:
        event = {
            "trace_id": result.trace_id,
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
