from __future__ import annotations

from pydantic import BaseModel, Field

from .budget import Budget, BudgetExceeded
from .catalog import DBCatalog, SelectionResult
from .llm import LLM, LLMError
from .prompts import SELECTOR_PROMPT


class SelectionOut(BaseModel):
    """选表结构化输出 schema（LangChain with_structured_output 用）。"""

    tables: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: str = "high"

TABLE_HINTS: dict[str, list[str]] = {
    "艺人": ["artists"],
    "歌手": ["artists"],
    "艺术家": ["artists"],
    "专辑": ["albums"],
    "歌曲": ["tracks"],
    "曲目": ["tracks"],
    "音乐": ["tracks"],
    "作曲": ["tracks"],
    "时长": ["tracks"],
    "流派": ["genres"],
    "风格": ["genres"],
    "摇滚": ["genres"],
    "爵士": ["genres"],
    "金属": ["genres"],
    "歌单": ["playlists", "playlist_track"],
    "播放列表": ["playlists", "playlist_track"],
    "客户": ["customers"],
    "顾客": ["customers"],
    "用户": ["customers"],
    "会员": ["customers"],
    "员工": ["employees"],
    "雇员": ["employees"],
    "经理": ["employees"],
    "下属": ["employees"],
    "上级": ["employees"],
    "客服": ["employees"],
    "发票": ["invoices"],
    "订单": ["invoices"],
    "账单": ["invoices"],
    "销售额": ["invoices"],
    "收入": ["invoices"],
    "营收": ["invoices"],
    "消费": ["invoices"],
    "金额": ["invoices"],
    "销量": ["invoice_items", "tracks"],
    "购买": ["invoices", "invoice_items"],
    "媒体": ["media_types"],
    "音频": ["media_types"],
    "格式": ["media_types"],
    "国家": ["invoices", "customers"],
}

METRIC_TABLE_HINTS: dict[str, list[str]] = {
    "revenue": ["invoices"],
    "revenue_line": ["invoice_items", "tracks"],
    "sales_volume": ["invoice_items", "tracks"],
    "customer_count": ["customers"],
    "customer_count_active": ["invoices", "customers"],
    "track_count": ["tracks"],
    "track_count_sold": ["invoice_items", "tracks"],
    "avg_spend_per_customer": ["invoices", "customers"],
}


class TableSelector:
    def __init__(
        self,
        catalog: DBCatalog,
        llm: LLM,
        max_tables: int = 8,
    ):
        self.catalog = catalog
        self.llm = llm
        self.max_tables = max(1, int(max_tables))

    def select(
        self,
        question: str,
        budget: Budget | None = None,
        semantic: object | None = None,
    ) -> SelectionResult:
        hints = self._hint_tables(question, semantic)
        catalog_text = self.catalog.compact_catalog()
        if hints:
            catalog_text += "\n\n关键词提示（仅供参考，需结合问题判断）: " + ", ".join(hints)

        messages = SELECTOR_PROMPT.format_messages(catalog=catalog_text, question=question)
        warnings: list[dict[str, str]] = []
        try:
            try:
                # 优先 LangChain 结构化输出；失败时降级为普通 JSON 解析
                data = self.llm.chat_structured(
                    messages,
                    SelectionOut,
                    max_tokens=400,
                    budget=budget,
                    step="selector",
                )
            except LLMError:
                if budget is not None and not budget.can_call():
                    raise
                data = self.llm.chat_json(
                    messages,
                    max_tokens=400,
                    budget=budget,
                    step="selector",
                )
            raw_tables = data.get("tables") or []
            reason = str(data.get("reason") or "").strip()
            confidence = str(data.get("confidence") or "high").lower()
            if confidence not in {"high", "medium", "low"}:
                confidence = "medium"
        except (LLMError, BudgetExceeded) as exc:
            warnings.append({"code": "E3001", "detail": f"LLM 选表失败，回退: {exc}"})
            return self._fallback(hints, "LLM 选表失败，回退关键词/全表", warnings)

        requested: list[str] = []
        for name in raw_tables:
            raw = str(name).strip()
            real = self.catalog.resolve_table(raw)
            if not real:
                warnings.append({"code": "E3003", "detail": f"忽略不存在的表: {raw}"})
                continue
            if real not in requested:
                requested.append(real)

        if not requested:
            warnings.append({"code": "E3002", "detail": "LLM 未选出有效表"})
            return self._fallback(hints, reason or "LLM 未选出有效表", warnings)

        connected = self.catalog.connect_tables(requested)
        bridges = [table for table in connected if table not in requested]
        if len(connected) > self.max_tables:
            allowed = requested[: self.max_tables]
            for table in bridges:
                if len(allowed) >= self.max_tables:
                    break
                allowed.append(table)
            connected = [table for table in connected if table in allowed]
            warnings.append(
                {
                    "code": "E5015",
                    "detail": f"关联表超过 {self.max_tables} 张，已截断到核心表",
                }
            )
        if bridges and (
            "playlist_track" in bridges or "invoice_items" in bridges
        ):
            warnings.append(
                {
                    "code": "E3005",
                    "detail": "存在一对多关联，聚合时注意去重，避免重复计数",
                }
            )
        return SelectionResult(
            tables=connected,
            reason=reason or "LLM 选表完成",
            used_fallback=False,
            requested_tables=requested,
            bridge_tables=[table for table in connected if table not in requested],
            confidence=confidence,
            warnings=warnings,
        )

    def _fallback(
        self, hints: list[str], reason: str, warnings: list[dict[str, str]]
    ) -> SelectionResult:
        if hints:
            connected = self.catalog.connect_tables(hints)
            return SelectionResult(
                tables=connected,
                reason=reason,
                used_fallback=True,
                requested_tables=list(hints),
                bridge_tables=[t for t in connected if t not in hints],
                confidence="low",
                warnings=warnings,
            )
        warnings.append(
            {"code": "E3004", "detail": "无法确定表，使用全部表，结果可能较慢"}
        )
        return SelectionResult(
            tables=list(self.catalog.table_names),
            reason=f"{reason}；使用全部表",
            used_fallback=True,
            requested_tables=[],
            bridge_tables=list(self.catalog.table_names),
            confidence="low",
            warnings=warnings,
            fallback_all=True,
        )

    def _hint_tables(self, question: str, semantic: object | None) -> list[str]:
        selected: list[str] = []
        for keyword, tables in TABLE_HINTS.items():
            if keyword in question:
                for table in tables:
                    if table not in selected:
                        selected.append(table)
        if semantic is not None and hasattr(semantic, "detect_metrics"):
            for _synonym, metric in semantic.detect_metrics(question):
                for table in METRIC_TABLE_HINTS.get(metric.id, []):
                    if table not in selected:
                        selected.append(table)
            if hasattr(semantic, "resolve_aliases"):
                try:
                    for alias in semantic.resolve_aliases(question, None, selected):
                        if alias.table not in selected:
                            selected.append(alias.table)
                except Exception:  # noqa: BLE001 - 提示失败不影响选表
                    pass
        return [table for table in selected if self.catalog.has_table(table)]

    @staticmethod
    def hint_tables(question: str) -> list[str]:
        selected: list[str] = []
        for keyword, tables in TABLE_HINTS.items():
            if keyword in question:
                for table in tables:
                    if table not in selected:
                        selected.append(table)
        return selected
