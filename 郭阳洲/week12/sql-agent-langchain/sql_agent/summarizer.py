from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .budget import Budget, BudgetExceeded
from .contracts import (
    Claim,
    EmptyClassification,
    allowed_values,
    template_answer,
    verify_answer_text,
    verify_claims,
)
from .executor import ExecutionResult
from .llm import LLM, LLMError, extract_json
from .prompts import SUMMARY_PROMPT


@dataclass
class SummaryResult:
    answer: str
    claims: list[Claim] = field(default_factory=list)
    grounding_ok: bool = True
    used_fallback: bool = False
    error_code: str = ""
    warnings: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "claims": [claim.to_dict() for claim in self.claims],
            "grounding_ok": self.grounding_ok,
            "used_fallback": self.used_fallback,
            "error_code": self.error_code,
            "warnings": list(self.warnings),
        }


class ResultSummarizer:
    def __init__(self, llm: LLM):
        self.llm = llm

    def summarize(
        self,
        question: str,
        sql: str,
        result: ExecutionResult,
        *,
        metric: dict[str, Any] | None = None,
        time_range: dict[str, Any] | None = None,
        data_until: str = "",
        assumptions: Iterable[str] = (),
        empty: EmptyClassification | None = None,
        budget: Budget | None = None,
    ) -> SummaryResult:
        assumptions = list(assumptions)
        allowed = allowed_values(result.columns, result.rows)
        fallback = template_answer(
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            truncated=result.truncated,
            metric=metric,
            data_until=data_until,
            assumptions=assumptions,
            empty=empty,
        )

        if empty is not None or not result.rows:
            return SummaryResult(fallback, used_fallback=True)

        preview = _markdown_table(result.columns, result.rows[:20])
        row_text = f"{result.row_count}+" if result.truncated else str(result.row_count)
        truncated_note = "（结果已截断，仅展示前 20 行预览）" if result.truncated else ""
        metric_text = _metric_text(metric)
        time_text = _time_text(time_range)
        assumption_text = "；".join(assumptions) if assumptions else "无"

        messages = SUMMARY_PROMPT.format_messages(
            question=question,
            sql=sql,
            row_count=row_text,
            truncated_note=truncated_note,
            metric=metric_text,
            time_range=time_text,
            data_until=data_until or "未知",
            assumptions=assumption_text,
            result_preview=preview,
        )

        if budget is not None and not budget.can_call():
            return SummaryResult(
                fallback,
                used_fallback=True,
                warnings=[{"code": "E8003", "detail": "预算不足，使用模板答案"}],
            )

        try:
            text = self.llm.chat(
                messages,
                temperature=0.0,
                max_tokens=1000,
                budget=budget,
                step="summarizer",
            )
            data = extract_json(text)
        except (LLMError, BudgetExceeded, ValueError) as exc:
            code = getattr(exc, "code", "E7001")
            return SummaryResult(
                fallback,
                used_fallback=True,
                error_code=code,
                warnings=[{"code": "E7001", "detail": f"汇总失败，使用模板: {exc}"}],
            )

        summary = str(data.get("summary") or "").strip()
        if not summary:
            return SummaryResult(
                fallback,
                used_fallback=True,
                error_code="E7001",
                warnings=[{"code": "E7001", "detail": "模型未返回 summary"}],
            )

        claims = _parse_claims(data.get("claims"))
        framing = [float(result.row_count), float(len(result.rows)), 20.0, 100.0]
        framing.extend(_column_sums(result.columns, result.rows))
        matched, unmatched = verify_claims(claims, allowed + framing)
        unmatched_text = verify_answer_text(summary, allowed, framing_values=framing)

        if unmatched or unmatched_text:
            detail = []
            if unmatched:
                detail.append("claims 数字无法核对: " + ", ".join(f"{v:g}" for v in unmatched))
            if unmatched_text:
                detail.append(
                    "正文数字无法核对: " + ", ".join(f"{v:g}" for v in unmatched_text)
                )
            return SummaryResult(
                fallback,
                claims=matched,
                grounding_ok=False,
                used_fallback=True,
                error_code="E7002",
                warnings=[{"code": "E7002", "detail": "；".join(detail)}],
            )

        return SummaryResult(summary, claims=matched, grounding_ok=True)

    def summarize_rows(
        self,
        question: str,
        sql: str,
        columns: list[str],
        rows: list[tuple[Any, ...]],
        truncated: bool = False,
        **kwargs: Any,
    ) -> SummaryResult:
        return self.summarize(
            question,
            sql,
            ExecutionResult(
                ok=True,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
            ),
            **kwargs,
        )


def _column_sums(columns: list[str], rows: list[tuple[Any, ...]]) -> list[float]:
    sums: list[float] = []
    for index in range(len(columns)):
        total = 0.0
        count = 0
        for row in rows:
            if index >= len(row):
                continue
            value = row[index]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            total += float(value)
            count += 1
        if count:
            sums.append(total)
    return sums


def _parse_claims(raw: Any) -> list[Claim]:
    claims: list[Claim] = []
    if not isinstance(raw, list):
        return claims
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        claims.append(
            Claim(
                text=str(item.get("text") or ""),
                value=value,
                source=str(item.get("source") or ""),
            )
        )
    return claims


def _metric_text(metric: dict[str, Any] | None) -> str:
    if not metric:
        return "无预定义指标（仅描述性统计）"
    parts = [str(metric.get("label") or metric.get("id") or "")]
    if metric.get("formula"):
        parts.append(f"公式 {metric['formula']}")
    if metric.get("unit"):
        parts.append(f"单位 {metric['unit']}")
    if metric.get("grain"):
        parts.append(f"粒度 {metric['grain']}")
    return "；".join(part for part in parts if part)


def _time_text(time_range: dict[str, Any] | None) -> str:
    if not time_range:
        return "未指定"
    label = time_range.get("label") or ""
    start = time_range.get("start") or "不限"
    end = time_range.get("end") or "不限"
    return f"{label}（{start} ~ {end}）"


def _markdown_table(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    if not columns:
        return "（无列信息）"
    header = "| " + " | ".join(_cell(c) for c in columns) + " |"
    divider = "|" + "|".join(["---"] * len(columns)) + "|"
    body = [
        "| " + " | ".join(_cell(value) for value in row) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def _cell(value: Any) -> str:
    if value is None:
        return "NULL"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text[:80] + ("…" if len(text) > 80 else "")
