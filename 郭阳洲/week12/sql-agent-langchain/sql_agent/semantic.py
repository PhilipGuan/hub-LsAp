from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from .errors import AgentError

LINE_DIMENSIONS = {"genres", "tracks", "artists", "albums", "media_types"}
SALES_METRICS = {
    "revenue",
    "revenue_line",
    "sales_volume",
    "track_count_sold",
    "customer_count_active",
}
CUSTOMER_METRICS = {"customer_count", "avg_spend_per_customer"}


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    label: str
    synonyms: tuple[str, ...]
    formula: str
    grain: str
    unit: str
    time_field: str | None = None
    country_field: str | None = None
    supports_time_scope: bool = True
    variant_of: str | None = None
    time_variant: str | None = None
    line_dimensions: tuple[str, ...] = ()
    notes: str = ""

    @property
    def is_variant(self) -> bool:
        return bool(self.variant_of)


@dataclass(frozen=True)
class AliasDefinition:
    mention: str
    table: str
    column: str
    value: str
    applies_to_metrics: tuple[str, ...] = ()


@dataclass
class ResolvedAlias:
    mention: str
    table: str
    column: str
    value: str
    matched_by: str = "exact"

    @property
    def condition(self) -> str:
        escaped = self.value.replace("'", "''")
        return f"{self.table}.{self.column} = '{escaped}'"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mention": self.mention,
            "table": self.table,
            "column": self.column,
            "value": self.value,
            "condition": self.condition,
            "matched_by": self.matched_by,
        }


@dataclass
class MetricBinding:
    metric: MetricDefinition
    assumptions: list[str] = field(default_factory=list)
    confidence: str = "high"
    country_field: str | None = None
    variant_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.metric.id,
            "label": self.metric.label,
            "formula": self.metric.formula,
            "grain": self.metric.grain,
            "unit": self.metric.unit,
            "country_field": self.country_field,
            "variant_of": self.metric.variant_of,
            "confidence": self.confidence,
            "assumptions": list(self.assumptions),
        }


@dataclass
class TimeRange:
    start: str | None
    end: str | None
    label: str
    granularity: str | None = None
    specified: bool = False
    assumption: str = ""
    out_of_range: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "label": self.label,
            "granularity": self.granularity,
            "specified": self.specified,
            "assumption": self.assumption,
            "out_of_range": self.out_of_range,
        }


@dataclass
class SemanticWarning:
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass
class SemanticResolution:
    question: str
    metric: MetricBinding | None
    time_range: TimeRange
    aliases: list[ResolvedAlias]
    assumptions: list[str]
    warnings: list[SemanticWarning]
    version: str
    data_from: str
    data_until: str

    @property
    def metric_id(self) -> str | None:
        return self.metric.metric.id if self.metric else None

    @property
    def grain(self) -> str | None:
        return self.metric.metric.grain if self.metric else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.to_dict() if self.metric else None,
            "time_range": self.time_range.to_dict(),
            "aliases": [a.to_dict() for a in self.aliases],
            "assumptions": list(self.assumptions),
            "warnings": [w.to_dict() for w in self.warnings],
            "version": self.version,
            "data_from": self.data_from,
            "data_until": self.data_until,
        }

    def prompt_block(self) -> str:
        if self.metric:
            metric = self.metric.metric
            metric_text = (
                f"绑定指标：{metric.id}（{metric.label}）\n"
                f"公式：{metric.formula}\n"
                f"粒度：{metric.grain}，单位：{metric.unit}\n"
                f"国家字段：{self.metric.country_field or '不适用'}"
            )
        else:
            metric_text = (
                "未绑定预定义指标：本题只做描述性统计，不得套用未定义的业务指标。"
            )

        time_text = (
            f"时间范围：{self.time_range.label}"
            f"（{self.time_range.start or '不限'} ~ {self.time_range.end or '不限'}）"
        )
        if self.time_range.granularity:
            time_text += f"，粒度：{self.time_range.granularity}"
        if self.time_range.out_of_range:
            time_text += "；该范围超出 data_until，必须明确说明数据截止"
        if not self.time_range.specified:
            time_text += "；用户未指定时间，使用全量数据并标注"

        alias_text = "无"
        if self.aliases:
            alias_text = "\n".join(f"- {a.mention} -> {a.condition}" for a in self.aliases)

        assumption_text = "无"
        if self.assumptions:
            assumption_text = "\n".join(f"- {item}" for item in self.assumptions)

        return (
            f"指标口径版本：{self.version}；金额单位 USD；"
            f"数据范围：{self.data_from} ~ {self.data_until}\n\n"
            f"{metric_text}\n\n"
            f"{time_text}\n\n"
            f"实体映射（必须使用精确值，不得改写）：\n{alias_text}\n\n"
            f"口径假设：\n{assumption_text}\n\n"
            "安全约束：禁止 SELECT *；禁止直接投影 Email/Phone/Address/Company/"
            "FirstName/LastName/City/State/PostalCode/Fax；需要展示客户或员工时使用 ID。"
        )


class MetricUndefinedError(AgentError):
    def __init__(self, mention: str, alternatives: list[str]):
        self.mention = mention
        self.alternatives = alternatives
        text = f"未定义指标「{mention}」"
        if alternatives:
            text += "；可用替代：" + "、".join(alternatives)
        super().__init__("E7005", text)


class MetricAmbiguousError(AgentError):
    def __init__(self, detail: str):
        super().__init__("E1005", detail)


class SemanticCatalog:
    def __init__(
        self,
        metrics: dict[str, MetricDefinition],
        aliases: list[AliasDefinition],
        dimension_aliases: list[AliasDefinition],
        undefined_metrics: dict[str, list[str]],
        version: str,
        data_from: str,
        data_until: str,
        currency: str = "USD",
        dimensions: list[dict[str, Any]] | None = None,
    ):
        self.metrics = metrics
        self.aliases = aliases
        self.dimension_aliases = dimension_aliases
        self.undefined_metrics = undefined_metrics
        self.version = version
        self.data_from = data_from
        self.data_until = data_until
        self.currency = currency
        self.dimensions = dimensions or []
        self._synonyms: list[tuple[str, MetricDefinition]] = []
        for metric in metrics.values():
            for synonym in metric.synonyms:
                if synonym:
                    self._synonyms.append((synonym, metric))
        self._synonyms.sort(key=lambda item: len(item[0]), reverse=True)
        self._undefined_sorted = sorted(
            self.undefined_metrics.items(), key=lambda item: len(item[0]), reverse=True
        )

    # ---------- loading ----------

    @classmethod
    def load(cls, semantic_dir: str | Path) -> "SemanticCatalog":
        directory = Path(semantic_dir)
        metrics_path = directory / "metrics.yaml"
        aliases_path = directory / "entity_aliases.yaml"
        if not metrics_path.exists():
            raise AgentError("E8006", f"缺少指标文件: {metrics_path}")
        if not aliases_path.exists():
            raise AgentError("E8006", f"缺少别名文件: {aliases_path}")
        try:
            metrics_raw = yaml.safe_load(metrics_path.read_text(encoding="utf-8")) or {}
            aliases_raw = yaml.safe_load(aliases_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise AgentError("E8006", f"语义 YAML 解析失败: {exc}") from exc

        metrics: dict[str, MetricDefinition] = {}
        for item in metrics_raw.get("metrics") or []:
            metric = MetricDefinition(
                id=str(item["id"]),
                label=str(item.get("label") or item["id"]),
                synonyms=tuple(item.get("synonyms") or ()),
                formula=str(item.get("formula") or ""),
                grain=str(item.get("grain") or ""),
                unit=str(item.get("unit") or ""),
                time_field=item.get("time_field"),
                country_field=item.get("country_field"),
                supports_time_scope=bool(item.get("supports_time_scope", True)),
                variant_of=item.get("variant_of"),
                time_variant=item.get("time_variant"),
                line_dimensions=tuple(item.get("line_dimensions") or ()),
                notes=str(item.get("notes") or ""),
            )
            if metric.id in metrics:
                raise AgentError("E8006", f"指标 id 重复: {metric.id}")
            metrics[metric.id] = metric

        aliases: list[AliasDefinition] = []
        for item in aliases_raw.get("entities") or []:
            value = item.get("value")
            if value is None:
                continue
            aliases.append(
                AliasDefinition(
                    mention=str(item["mention"]),
                    table=str(item["table"]),
                    column=str(item["column"]),
                    value=str(value),
                    applies_to_metrics=tuple(item.get("applies_to_metrics") or ()),
                )
            )

        dimension_aliases: list[AliasDefinition] = []
        for item in aliases_raw.get("dimension_aliases") or []:
            dimension_aliases.append(
                AliasDefinition(
                    mention=str(item["mention"]),
                    table=str(item["table"]),
                    column=str(item["column"]),
                    value="",
                )
            )

        undefined = {
            str(item["mention"]): [str(v) for v in item.get("alternatives") or []]
            for item in metrics_raw.get("undefined_metrics") or []
        }

        catalog = cls(
            metrics=metrics,
            aliases=aliases,
            dimension_aliases=dimension_aliases,
            undefined_metrics=undefined,
            version=str(metrics_raw.get("version") or "metrics-v0"),
            data_from=str(metrics_raw.get("data_from") or "2009-01-01"),
            data_until=str(metrics_raw.get("data_until") or "2013-12-22"),
            currency=str(metrics_raw.get("currency") or "USD"),
            dimensions=list(metrics_raw.get("dimensions") or []),
        )
        catalog.validate()
        return catalog

    def validate(self) -> None:
        mains = [m for m in self.metrics.values() if not m.is_variant]
        if len(mains) != 5:
            raise AgentError(
                "E8006", f"指标文件必须定义 5 个主指标，实际 {len(mains)} 个"
            )
        for metric in self.metrics.values():
            if metric.variant_of and metric.variant_of not in self.metrics:
                raise AgentError(
                    "E8006", f"变体 {metric.id} 引用了不存在的指标 {metric.variant_of}"
                )
            if not metric.formula:
                raise AgentError("E8006", f"指标 {metric.id} 缺少 formula")
        if len(self.dimensions) != 1:
            raise AgentError("E8006", "必须定义且只定义 1 个维度规则")
        _date(self.data_from)
        _date(self.data_until)

    # ---------- metric resolution ----------

    @property
    def main_metrics(self) -> list[MetricDefinition]:
        return [m for m in self.metrics.values() if not m.is_variant]

    def metric(self, metric_id: str) -> MetricDefinition | None:
        return self.metrics.get(metric_id)

    def detect_metrics(self, question: str) -> list[tuple[str, MetricDefinition]]:
        found: list[tuple[str, MetricDefinition]] = []
        seen: set[str] = set()
        for synonym, metric in self._synonyms:
            if synonym in question and metric.id not in seen:
                found.append((synonym, metric))
                seen.add(metric.id)
        return found

    def detect_undefined(self, question: str) -> tuple[str, list[str]] | None:
        for mention, alternatives in self._undefined_sorted:
            if mention.lower() in question.lower():
                return mention, alternatives
        return None

    def bind_metric(
        self, question: str, selected_tables: list[str] | None = None
    ) -> MetricBinding | None:
        undefined = self.detect_undefined(question)
        if undefined:
            raise MetricUndefinedError(*undefined)

        found = self.detect_metrics(question)
        if not found:
            return None

        unique_ids = {metric.id for _, metric in found}
        if len(unique_ids) > 1:
            families = {self._family(metric_id) for metric_id in unique_ids}
            if len(families) > 1 and any(
                connector in question
                for connector in ("和", "与", "以及", "、", "同时", "分别")
            ):
                labels = "、".join(
                    self.metrics[metric_id].label for metric_id in sorted(unique_ids)
                )
                raise MetricAmbiguousError(
                    f"问题同时命中多个指标（{labels}）；本期一次只支持一个指标，请拆分提问"
                )

        metric = self._choose_metric(question, found)
        assumptions: list[str] = []
        confidence = "high"
        reason = ""

        tables = set(selected_tables or [])
        if metric.id == "revenue" and tables & LINE_DIMENSIONS:
            metric = self.metrics["revenue_line"]
            reason = "维度属于曲目/流派/艺人/专辑/媒体类型"
            assumptions.append(
                "销售额按行粒度统计（invoice_items.UnitPrice * Quantity），与账单粒度不可混算"
            )
            confidence = "medium"

        if metric.id == "track_count" and any(
            word in question for word in ("已售", "售出", "卖出", "卖了多少")
        ):
            metric = self.metrics["track_count_sold"]
            assumptions.append("歌曲数按已售曲目统计（COUNT DISTINCT invoice_items.TrackId）")

        return MetricBinding(
            metric=metric,
            assumptions=assumptions,
            confidence=confidence,
            country_field=metric.country_field,
            variant_reason=reason,
        )

    def _choose_metric(
        self, question: str, found: list[tuple[str, MetricDefinition]]
    ) -> MetricDefinition:
        ids = {metric.id for _, metric in found}
        if "avg_spend_per_customer" in ids and any(
            word in question for word in ("人均", "每位客户平均", "平均每位客户")
        ):
            return self.metrics["avg_spend_per_customer"]
        if "sales_volume" in ids and any(
            word in question for word in ("销量", "售出数量", "销售数量")
        ):
            return self.metrics["sales_volume"]
        if "customer_count_active" in ids:
            return self.metrics["customer_count_active"]
        if "customer_count" in ids:
            return self.metrics["customer_count"]
        if "track_count_sold" in ids:
            return self.metrics["track_count_sold"]
        if "track_count" in ids:
            return self.metrics["track_count"]
        # 同长度同义词时，优先更具体的指标
        priority = {
            "avg_spend_per_customer": 0,
            "sales_volume": 1,
            "customer_count": 2,
            "track_count": 3,
            "revenue": 4,
            "revenue_line": 5,
        }
        return min((metric for _, metric in found), key=lambda m: priority.get(m.id, 99))

    def _family(self, metric_id: str) -> str:
        metric = self.metrics[metric_id]
        return metric.variant_of or metric_id

    # ---------- top-level ----------

    def resolve(
        self, question: str, selected_tables: list[str] | None = None
    ) -> SemanticResolution:
        binding = self.bind_metric(question, selected_tables)
        time_range = self.parse_time_range(question)
        assumptions: list[str] = []
        warnings: list[SemanticWarning] = []

        if binding and time_range.specified and not binding.metric.supports_time_scope:
            variant_id = binding.metric.time_variant
            if variant_id and variant_id in self.metrics:
                binding = replace(
                    binding,
                    metric=self.metrics[variant_id],
                    country_field=self.metrics[variant_id].country_field,
                    confidence="medium" if binding.confidence == "high" else "low",
                )
                assumptions.append(
                    f"指标「{binding.metric.label}」在指定时间范围下按变体口径统计"
                )
                warnings.append(
                    SemanticWarning(
                        "E7006",
                        f"{binding.metric.label}存在时间口径差异，已按变体默认口径生成",
                    )
                )
            else:
                raise MetricAmbiguousError(
                    f"指标「{binding.metric.label}」不支持在指定时间范围内统计，请补充口径"
                )

        if binding:
            assumptions.extend(binding.assumptions)
            if binding.metric.id == "revenue":
                assumptions.append("销售额按账单粒度统计（invoices.Total，USD）")
            elif binding.metric.id == "revenue_line":
                assumptions.append("销售额按行粒度统计并标注，不与账单销售额混算")
            elif binding.metric.id == "avg_spend_per_customer":
                assumptions.append("人均消费 = SUM(invoices.Total) / COUNT(DISTINCT invoices.CustomerId)")

        if not time_range.specified:
            assumptions.append(
                f"未指定时间范围，使用全量数据（{self.data_from} ~ {self.data_until}）"
            )
            warnings.append(
                SemanticWarning("E7007", "未指定时间范围，已使用全量数据并标注")
            )
        elif time_range.out_of_range:
            warnings.append(
                SemanticWarning(
                    "E7003",
                    f"查询时间晚于数据截止 {self.data_until}，需要明确说明数据未覆盖",
                )
            )

        aliases = self.resolve_aliases(question, binding, selected_tables, warnings)
        for alias in aliases:
            if alias.matched_by == "fuzzy":
                assumptions.append(f"实体「{alias.mention}」按模糊匹配映射为 {alias.value}")
                warnings.append(
                    SemanticWarning("E3007", f"实体「{alias.mention}」为模糊匹配")
                )

        return SemanticResolution(
            question=question,
            metric=binding,
            time_range=time_range,
            aliases=aliases,
            assumptions=assumptions,
            warnings=warnings,
            version=self.version,
            data_from=self.data_from,
            data_until=self.data_until,
        )

    # ---------- aliases ----------

    def resolve_aliases(
        self,
        question: str,
        binding: MetricBinding | None,
        selected_tables: list[str] | None,
        warnings: list[SemanticWarning] | None = None,
    ) -> list[ResolvedAlias]:
        warnings = warnings if warnings is not None else []
        tables = set(selected_tables or [])
        metric_id = binding.metric.id if binding else None
        matched_ids = {metric_id} if metric_id else set(SALES_METRICS | CUSTOMER_METRICS)

        candidates: list[AliasDefinition] = []
        for alias in self.aliases:
            if alias.mention.lower() not in question.lower():
                continue
            if alias.applies_to_metrics and not (
                set(alias.applies_to_metrics) & matched_ids
            ):
                continue
            candidates.append(alias)

        # 同一 mention 多个候选时按上下文选一个
        by_mention: dict[str, list[AliasDefinition]] = {}
        for alias in candidates:
            by_mention.setdefault(alias.mention, []).append(alias)

        resolved: list[ResolvedAlias] = []
        for mention, options in by_mention.items():
            chosen = self._choose_alias(options, binding, tables, question)
            if chosen is None:
                continue
            if len({(o.table, o.column, o.value) for o in options}) > 1:
                warnings.append(
                    SemanticWarning(
                        "E3007",
                        f"实体「{mention}」存在多个候选，已按指标口径选择 {chosen.table}.{chosen.column}",
                    )
                )
            resolved.append(
                ResolvedAlias(
                    mention=mention,
                    table=chosen.table,
                    column=chosen.column,
                    value=chosen.value,
                    matched_by="exact",
                )
            )

        # 用户直接输入英文值（如 Rock / USA）时也做确定性映射
        known_values = {(a.table, a.value) for a in self.aliases}
        for table, value in known_values:
            if not value or len(value) < 2:
                continue
            if value.lower() in question.lower():
                if any(r.value.lower() == value.lower() for r in resolved):
                    continue
                if table in tables or not tables:
                    existing = next(
                        (a for a in self.aliases if a.table == table and a.value == value),
                        None,
                    )
                    if existing:
                        resolved.append(
                            ResolvedAlias(
                                mention=value,
                                table=existing.table,
                                column=existing.column,
                                value=existing.value,
                                matched_by="literal",
                            )
                        )
        return resolved

    def _choose_alias(
        self,
        options: list[AliasDefinition],
        binding: MetricBinding | None,
        tables: set[str],
        question: str = "",
    ) -> AliasDefinition | None:
        if binding and binding.country_field:
            field_table, _, field_column = binding.country_field.partition(".")
            for option in options:
                if (
                    option.table.lower() == field_table.lower()
                    and option.column.lower() == field_column.lower()
                ):
                    return option
        if binding and binding.metric.id in CUSTOMER_METRICS:
            for option in options:
                if option.table == "customers":
                    return option
        if binding and binding.metric.id in SALES_METRICS:
            for option in options:
                if option.table == "invoices":
                    return option
        # 无指标绑定时按问题措辞选国家字段
        if "customers" in tables and any(
            cue in question for cue in ("客户", "用户", "顾客")
        ):
            for option in options:
                if option.table == "customers":
                    return option
        preferred = [option for option in options if option.table in tables]
        if preferred:
            return preferred[0]
        return options[0] if options else None

    # ---------- time ----------

    def parse_time_range(self, question: str) -> TimeRange:
        anchor = _date(self.data_until)
        granularity = _granularity(question)
        text = question.strip()

        # 1) 显式年月
        month_match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
        if month_match:
            year, month = int(month_match.group(1)), int(month_match.group(2))
            start, end = _month_bounds(year, month)
            end = min(end, anchor)
            label = f"{year}-{month:02d}"
            return self._time_range(start, end, label, granularity, True, anchor)

        # 2) 年份范围
        range_match = re.search(
            r"(\d{4})\s*(?:年)?\s*(?:到|至|~|—|-)\s*(\d{4})\s*年?", text
        )
        if range_match:
            year_a, year_b = int(range_match.group(1)), int(range_match.group(2))
            start_year, end_year = min(year_a, year_b), max(year_a, year_b)
            start = date(start_year, 1, 1)
            end = date(end_year, 12, 31)
            return self._time_range(
                start, min(end, anchor), f"{start_year} 至 {end_year}", granularity, True, anchor
            )

        # 3) 相对时间
        relative = self._relative_time(text, anchor, granularity)
        if relative:
            return relative

        # 4) 显式年份 + 上/下半年
        half_match = re.search(r"(\d{4})\s*年?\s*(上半年|下半年)", text)
        if half_match:
            year = int(half_match.group(1))
            half = half_match.group(2)
            if half == "上半年":
                start, end = date(year, 1, 1), date(year, 6, 30)
            else:
                start, end = date(year, 7, 1), date(year, 12, 31)
            return self._time_range(
                start, end, f"{year} {half}", granularity, True, anchor
            )

        # 5) 单年（含“以后/以前/至今”）
        year_match = re.search(r"(\d{4})\s*年?(以后|之后|起|以前|之前|至今|到现在)?", text)
        if year_match:
            year = int(year_match.group(1))
            qualifier = year_match.group(2) or ""
            start = date(year, 1, 1)
            end = date(year, 12, 31)
            if qualifier in {"以后", "之后", "起", "至今", "到现在"}:
                end = max(anchor, start)
                label = f"{year}-01-01 以后"
                return self._time_range(start, end, label, granularity, True, anchor)
            if qualifier in {"以前", "之前"}:
                start = _date(self.data_from)
                end = date(year, 12, 31)
                label = f"{year}-12-31 以前"
                return self._time_range(start, end, label, granularity, True, anchor)
            label = str(year)
            return self._time_range(start, min(end, anchor), label, granularity, True, anchor)

        # 5) 未指定
        start = _date(self.data_from)
        return TimeRange(
            start=start.isoformat(),
            end=anchor.isoformat(),
            label=f"全量数据（{self.data_from} ~ {self.data_until}）",
            granularity=granularity,
            specified=False,
            assumption="未指定时间范围，使用全量数据",
            out_of_range=False,
        )

    def _relative_time(
        self, text: str, anchor: date, granularity: str | None
    ) -> TimeRange | None:
        if last_years := re.search(r"最近\s*(\d+)\s*年", text):
            n = max(1, int(last_years.group(1)))
            start = date(anchor.year - n + 1, 1, 1)
            label = f"最近 {n} 年（以 {self.data_until} 为锚）"
            return self._time_range(start, anchor, label, granularity, True, anchor)
        if "最近一年" in text or "近一年" in text:
            start = _add_months(anchor, -12)
            label = f"最近一年（以 {self.data_until} 为锚）"
            return self._time_range(start, anchor, label, granularity, True, anchor)

        last_months = re.search(r"最近\s*(\d+)\s*个?月", text)
        if last_months:
            n = max(1, int(last_months.group(1)))
            start = _add_months(anchor.replace(day=1), -(n - 1))
            label = f"最近 {n} 个月（以 {self.data_until} 为锚）"
            return self._time_range(start, anchor, label, "month", True, anchor)

        last_days = re.search(r"最近\s*(\d+)\s*天", text)
        if last_days:
            n = max(1, int(last_days.group(1)))
            start = anchor - timedelta(days=n - 1)
            label = f"最近 {n} 天（以 {self.data_until} 为锚）"
            return self._time_range(start, anchor, label, "day", True, anchor)

        if "上个月" in text or "上月" in text:
            start = _add_months(anchor.replace(day=1), -1)
            end = _month_bounds(start.year, start.month)[1]
            return self._time_range(start, end, f"{start.year}-{start.month:02d}", "month", True, anchor)
        if "本月" in text or "这个月" in text:
            start = anchor.replace(day=1)
            return self._time_range(start, anchor, f"{anchor.year}-{anchor.month:02d}", "month", True, anchor)
        if (
            half_match := re.search(r"(今年|去年|前年)\s*(上半年|下半年)", text)
        ):
            year = anchor.year - {"今年": 0, "去年": 1, "前年": 2}[half_match.group(1)]
            half = half_match.group(2)
            if half == "上半年":
                start, end = date(year, 1, 1), date(year, 6, 30)
            else:
                start, end = date(year, 7, 1), date(year, 12, 31)
            return self._time_range(
                start, end, f"{year} {half}", granularity, True, anchor
            )
        if "今年" in text or "本年" in text:
            start = date(anchor.year, 1, 1)
            return self._time_range(start, anchor, str(anchor.year), granularity, True, anchor)
        if "去年" in text:
            year = anchor.year - 1
            return self._time_range(date(year, 1, 1), date(year, 12, 31), str(year), granularity, True, anchor)
        if "前年" in text:
            year = anchor.year - 2
            return self._time_range(date(year, 1, 1), date(year, 12, 31), str(year), granularity, True, anchor)
        if "明年" in text or "下一年" in text:
            year = anchor.year + 1
            return self._time_range(date(year, 1, 1), date(year, 12, 31), str(year), granularity, True, anchor)
        if "近几年" in text or "近年" in text:
            start = date(anchor.year - 2, 1, 1)
            return self._time_range(start, anchor, "最近 3 年", granularity, True, anchor)
        return None

    def _time_range(
        self,
        start: date,
        end: date,
        label: str,
        granularity: str | None,
        specified: bool,
        anchor: date,
    ) -> TimeRange:
        out_of_range = start > anchor
        if end > anchor and not out_of_range:
            end = anchor
        assumption = f"时间以 data_until={anchor.isoformat()} 为锚"
        return TimeRange(
            start=start.isoformat(),
            end=end.isoformat(),
            label=label,
            granularity=granularity,
            specified=specified,
            assumption=assumption,
            out_of_range=out_of_range,
        )


# ---------- date helpers ----------


def _date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + (value.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _granularity(question: str) -> str | None:
    if any(word in question for word in ("每月", "月度", "按月", "每个月")):
        return "month"
    if any(word in question for word in ("每季度", "季度", "按季")):
        return "quarter"
    if any(word in question for word in ("每年", "年度", "按年", "每年")):
        return "year"
    if any(word in question for word in ("每天", "按天", "每日")):
        return "day"
    return None
