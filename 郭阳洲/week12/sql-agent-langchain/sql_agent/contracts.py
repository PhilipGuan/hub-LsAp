from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Callable, Iterable

from .executor import ExecutionResult
from .privacy import mask_rows


class ResultStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    TRUNCATED = "truncated"
    ERROR = "error"


@dataclass
class EmptyClassification:
    reason: str
    message: str
    code: str = "E7003"

    def to_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message, "code": self.code}


@dataclass
class ResultContract:
    status: str
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    metric: dict[str, Any] | None = None
    time_range: dict[str, Any] | None = None
    data_until: str = ""
    warnings: list[dict[str, Any]] = field(default_factory=list)
    trace_id: str = ""
    empty_reason: str | None = None
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "row_count": self.row_count,
            "truncated": self.truncated,
            "metric": self.metric,
            "time_range": self.time_range,
            "data_until": self.data_until,
            "warnings": list(self.warnings),
            "trace_id": self.trace_id,
            "empty_reason": self.empty_reason,
            "error_code": self.error_code,
        }


def classify_empty(
    columns: list[str],
    rows: list[tuple[Any, ...]],
    *,
    out_of_range: bool,
    data_until: str,
    probe: Callable[[], bool] | None = None,
) -> EmptyClassification | None:
    if out_of_range:
        return EmptyClassification(
            "out_of_range",
            f"数据截止 {data_until}，无法覆盖该时间范围。",
        )
    if rows:
        if len(rows) == 1 and len(columns) == 1:
            value = rows[0][0]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
                return EmptyClassification("true_zero", f"结果为 0（数据截止 {data_until}）。")
        return None
    if probe is not None:
        try:
            if probe():
                return EmptyClassification(
                    "conditions_too_strict",
                    f"没有查到符合条件的数据，可能是筛选条件过严；可以放宽国家、流派或时间。数据截止 {data_until}。",
                )
        except Exception:  # noqa: BLE001 - 探测失败不应阻断答案
            pass
    return EmptyClassification(
        "unknown",
        f"没有查到符合条件的数据。数据截止 {data_until}。",
    )


def build_contract(
    execution: ExecutionResult,
    *,
    metric: dict[str, Any] | None,
    time_range: dict[str, Any] | None,
    data_until: str,
    warnings: Iterable[dict[str, Any]],
    trace_id: str,
    empty: EmptyClassification | None = None,
    error_code: str = "",
) -> ResultContract:
    if not execution.ok:
        status = ResultStatus.ERROR.value
    elif empty is not None:
        status = ResultStatus.EMPTY.value
    elif execution.truncated:
        status = ResultStatus.TRUNCATED.value
    else:
        status = ResultStatus.OK.value
    return ResultContract(
        status=status,
        columns=list(execution.columns),
        rows=list(execution.rows),
        row_count=execution.row_count,
        truncated=execution.truncated,
        metric=metric,
        time_range=time_range,
        data_until=data_until,
        warnings=list(warnings),
        trace_id=trace_id,
        empty_reason=empty.reason if empty else None,
        error_code=error_code,
    )


@dataclass
class SanityIssue:
    code: str
    detail: str
    severity: str = "warning"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail, "severity": self.severity}


_ADDITIVE = re.compile(
    r"(count|数量|销量|revenue|total|amount|spent|sales|qty|quantity|seconds)",
    re.I,
)


def check_sanity(
    columns: list[str],
    rows: list[tuple[Any, ...]],
    *,
    base_row_count: int | None = None,
    truncated: bool = False,
) -> list[SanityIssue]:
    issues: list[SanityIssue] = []
    if not rows:
        return issues

    for index, column in enumerate(columns):
        values = [row[index] for row in rows if index < len(row)]
        numeric = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if values and all(v is None for v in values):
            issues.append(SanityIssue("E7008", f"字段 {column} 全为 NULL", "warning"))
            continue
        if not numeric:
            continue
        if any(v < 0 for v in numeric) and _ADDITIVE.search(column):
            issues.append(
                SanityIssue("E7008", f"字段 {column} 出现负值，请检查口径", "warning")
            )
        if base_row_count is not None and re.search(r"(count|数量|销量|qty|quantity)", column, re.I):
            if max(numeric) > base_row_count:
                issues.append(
                    SanityIssue(
                        "E7008",
                        f"字段 {column} 的最大值 {max(numeric)} 超过基表行数 {base_row_count}",
                        "error",
                    )
                )

    if base_row_count is not None and len(rows) > base_row_count:
        issues.append(
            SanityIssue(
                "E7008",
                f"返回 {len(rows)} 行，超过基表行数 {base_row_count}，可能存在重复计数",
                "error",
            )
        )

    if not truncated and len({tuple(row) for row in rows}) < len(rows):
        if any(_ADDITIVE.search(column) for column in columns):
            issues.append(
                SanityIssue("E7008", "结果中存在完全重复的行，请检查 fan-out", "warning")
            )
    return issues


@dataclass
class TimeFillResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    filled: int = 0
    assumption: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": [list(row) for row in self.rows],
            "filled": self.filled,
            "assumption": self.assumption,
        }


_MONTH_COLUMNS = {"month", "月份", "年月", "date", "日期", "month_name"}
_MONTH_VALUE = re.compile(r"^(\d{4})-(\d{2})(?:-\d{2})?$")


def fill_time_series(
    columns: list[str],
    rows: list[tuple[Any, ...]],
    *,
    start: str | None,
    end: str | None,
    granularity: str | None,
) -> TimeFillResult:
    if granularity != "month" or not start or not end or not rows:
        return TimeFillResult(list(columns), list(rows))
    time_index = None
    for index, column in enumerate(columns):
        if column.lower() in _MONTH_COLUMNS or any(
            _MONTH_VALUE.match(str(row[index] or "")) for row in rows[:3] if index < len(row)
        ):
            time_index = index
            break
    if time_index is None:
        return TimeFillResult(list(columns), list(rows))

    first, last = _month_key(start), _month_key(end)
    if not first or not last or first > last:
        return TimeFillResult(list(columns), list(rows))
    months = _month_range(first, last)
    if not months or len(months) > 120:
        return TimeFillResult(list(columns), list(rows))

    existing: dict[str, tuple[Any, ...]] = {}
    for row in rows:
        key = str(row[time_index] or "")[:7]
        if _MONTH_VALUE.match(key):
            existing.setdefault(key, row)
    if not existing:
        return TimeFillResult(list(columns), list(rows))

    numeric_indexes = [
        index
        for index in range(len(columns))
        if index != time_index
        and any(
            isinstance(row[index], (int, float)) and not isinstance(row[index], bool)
            for row in rows
            if index < len(row)
        )
    ]
    filled_rows: list[tuple[Any, ...]] = []
    added = 0
    for key in months:
        if key in existing:
            filled_rows.append(existing[key])
            continue
        values: list[Any] = [None] * len(columns)
        values[time_index] = key
        for index in numeric_indexes:
            values[index] = 0
        filled_rows.append(tuple(values))
        added += 1
    assumption = "缺失月份按 0 补齐（时间补零口径）" if added else ""
    return TimeFillResult(list(columns), filled_rows, added, assumption)


def _month_key(value: str) -> str | None:
    match = re.match(r"^(\d{4})-(\d{1,2})", value or "")
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"


def _month_range(start: str, end: str) -> list[str]:
    start_year, start_month = int(start[:4]), int(start[5:7])
    end_year, end_month = int(end[:4]), int(end[5:7])
    index = start_year * 12 + start_month - 1
    last = end_year * 12 + end_month - 1
    months: list[str] = []
    while index <= last and len(months) <= 120:
        year, month = divmod(index, 12)
        months.append(f"{year:04d}-{month + 1:02d}")
        index += 1
    return months


@dataclass
class Claim:
    text: str
    value: float
    source: str = ""
    tolerance: float = 0.01

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "value": self.value,
            "source": self.source,
            "tolerance": self.tolerance,
        }


@dataclass
class GroundingResult:
    ok: bool
    claims: list[Claim] = field(default_factory=list)
    unmatched: list[float] = field(default_factory=list)
    unmatched_text_numbers: list[float] = field(default_factory=list)
    allowed_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "claims": [c.to_dict() for c in self.claims],
            "unmatched": self.unmatched,
            "unmatched_text_numbers": self.unmatched_text_numbers,
            "allowed_count": self.allowed_count,
        }


def numeric_values(columns: list[str], rows: list[tuple[Any, ...]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        for value in row:
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                values.append(float(value))
    return values


def allowed_values(
    columns: list[str],
    rows: list[tuple[Any, ...]],
    *,
    extra: Iterable[float] = (),
) -> list[float]:
    values = numeric_values(columns, rows)
    totals: dict[int, float] = {}
    for index in range(len(columns)):
        column_values = [
            float(row[index])
            for row in rows
            if index < len(row)
            and isinstance(row[index], (int, float))
            and not isinstance(row[index], bool)
        ]
        if column_values:
            total = float(sum(column_values))
            totals[index] = total
            values.extend(
                [
                    total,
                    float(len(column_values)),
                    total / len(column_values),
                    float(min(column_values)),
                    float(max(column_values)),
                ]
            )
    for index, total in totals.items():
        if total:
            for row in rows:
                if index < len(row) and isinstance(row[index], (int, float)):
                    values.append(float(row[index]) / total * 100)
    values.extend([float(v) for v in extra])
    values.extend([float(len(rows)), float(len(columns)), 100.0, 0.0])
    return values


def match_number(value: float, allowed: Iterable[float], tolerance: float = 0.011) -> bool:
    for candidate in allowed:
        if abs(value - candidate) <= tolerance:
            return True
        if candidate and abs(value - candidate) / abs(candidate) <= 0.001:
            return True
    return False


def verify_claims(
    claims: list[Claim], allowed: Iterable[float]
) -> tuple[list[Claim], list[float]]:
    allowed = list(allowed)
    matched: list[Claim] = []
    unmatched: list[float] = []
    for claim in claims:
        try:
            value = float(claim.value)
        except (TypeError, ValueError):
            unmatched.append(float("nan"))
            continue
        if match_number(value, allowed, max(0.011, claim.tolerance)):
            matched.append(claim)
        else:
            unmatched.append(value)
    return matched, unmatched


_NUMBER = re.compile(r"(?<![\d.A-Za-z\-])-?\d+(?:,\d{3})*(?:\.\d+)?%?")
_DATE_LITERAL = re.compile(
    r"\d{4}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?"
    r"|\d{4}\s*[-/]\s*\d{1,2}(?:\s*[-/]\s*\d{1,2})?"
    r"|(?<!\d)\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?"
)


def extract_numbers(text: str) -> list[float]:
    numbers: list[float] = []
    text = _DATE_LITERAL.sub(" ", text or "")
    for token in _NUMBER.findall(text):
        percent = token.endswith("%")
        cleaned = token.rstrip("%").replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        numbers.append(value)
    return numbers


def verify_answer_text(
    text: str,
    allowed: Iterable[float],
    *,
    framing_values: Iterable[float] = (),
) -> list[float]:
    allowed_list = list(allowed) + [float(v) for v in framing_values]
    unmatched: list[float] = []
    for value in extract_numbers(text):
        if 2000 <= value <= 2100 and value == int(value):
            continue
        if value == int(value) and 0 <= value <= 10:
            continue
        if match_number(value, allowed_list):
            continue
        unmatched.append(value)
    return unmatched


def format_number(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float):
        if abs(value - round(value)) < 1e-9:
            return f"{int(round(value)):,}"
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def template_answer(
    *,
    columns: list[str],
    rows: list[tuple[Any, ...]],
    row_count: int,
    truncated: bool,
    metric: dict[str, Any] | None,
    data_until: str,
    assumptions: Iterable[str],
    empty: EmptyClassification | None = None,
    error_message: str = "",
) -> str:
    assumptions = list(assumptions)
    suffix_parts = [f"数据截止 {data_until}。"]
    if assumptions:
        suffix_parts.append("口径：" + "；".join(assumptions[:3]) + "。")
    suffix = " ".join(suffix_parts)

    if error_message:
        return error_message
    if empty is not None:
        return empty.message

    if len(rows) == 1 and len(columns) == 1:
        label = metric.get("label") if metric else columns[0]
        unit = metric.get("unit") if metric else ""
        value = format_number(rows[0][0])
        unit_text = f" {unit}" if unit else ""
        return f"{label}为 {value}{unit_text}。{suffix}"

    header = "| " + " | ".join(str(column) for column in columns) + " |"
    divider = "|" + "|".join(["---"] * len(columns)) + "|"
    preview_rows = rows[:20]
    body = [
        "| " + " | ".join(format_number(value) for value in row) + " |"
        for row in preview_rows
    ]
    table = "\n".join([header, divider, *body])
    note = "（结果已截断，仅展示前 100 行）" if truncated else ""
    more = f"，此处展示前 {len(preview_rows)} 行" if len(rows) > len(preview_rows) else ""
    label = f"{metric.get('label')}：" if metric else ""
    return (
        f"{label}共查询到 {row_count} 行结果{note}{more}。\n\n{table}\n\n{suffix}"
    )


def sanitize_execution_rows(
    columns: list[str], rows: list[tuple[Any, ...]]
) -> list[tuple[Any, ...]]:
    return mask_rows(columns, rows)
