from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from sql_agent.agent import SQLAgent  # noqa: E402
from sql_agent.catalog import DBCatalog  # noqa: E402
from sql_agent.config import Settings, load_settings  # noqa: E402
from sql_agent.executor import ExecutionResult, SQLExecutor  # noqa: E402
from sql_agent.intent import IntentGate  # noqa: E402
from sql_agent.validator import SQLValidator  # noqa: E402

try:
    from sqlglot import exp, parse
except ImportError:  # pragma: no cover
    exp = None
    parse = None

EVAL_MAX_ROWS = 1000
TRUNC_MAX_ROWS = 100
ANALYTICS_SPLITS = {"dev", "holdout", "boundary", "entity"}
CORE_TAGS = ("单表", "join", "多对多", "时间", "空结果", "值映射")
GATES = {
    "holdout_total": 29,
    "dev_total": 28,
    "core_tag_pass": 4,
    "core_tag_size": 5,
    "selection_precision": 0.85,
    "selection_recall": 0.95,
    "fallback_precision": 0.70,
    "stability_span": 2,
}


# ---------------------------------------------------------------- gold loading


def load_gold(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{number} JSON 解析失败: {exc}") from exc
        item.setdefault("split", "dev")
        item.setdefault("tags", [])
        item.setdefault("required_tables", [])
        item.setdefault("allowed_extra_tables", [])
        item.setdefault("forbidden_tables", [])
        item.setdefault("ordered", False)
        item.setdefault("required_columns", [])
        item.setdefault("strict_columns", False)
        item.setdefault("tolerance_abs", 0.01)
        item.setdefault("tolerance_rel", 0.001)
        items.append(item)
    return items


def filter_items(
    items: list[dict[str, Any]],
    splits: set[str] | None,
    ids: set[str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    result = items
    if splits:
        result = [item for item in result if item["split"] in splits]
    if ids:
        result = [item for item in result if item["id"] in ids]
    if limit:
        result = result[:limit]
    return result


# ---------------------------------------------------------------- verification


def verify_gold(
    items: list[dict[str, Any]],
    catalog: DBCatalog,
    *,
    verbose: bool = True,
) -> list[dict[str, str]]:
    executor = SQLExecutor(catalog.db_path, max_rows=100_000, timeout_s=30)
    validator = SQLValidator(catalog)
    gate = IntentGate()
    problems: list[dict[str, str]] = []

    for item in items:
        item_id = item["id"]
        sql = item.get("gold_sql")
        if sql:
            execution = executor.execute(sql)
            if not execution.ok:
                problems.append(
                    {
                        "id": item_id,
                        "kind": "gold_sql",
                        "detail": f"{execution.error_code}: {execution.error}",
                    }
                )
            else:
                status = item.get("expected_status")
                if status == "truncated":
                    truncated = SQLExecutor(
                        catalog.db_path, max_rows=TRUNC_MAX_ROWS, timeout_s=30
                    ).execute(sql)
                    if not truncated.truncated:
                        problems.append(
                            {
                                "id": item_id,
                                "kind": "gold_status",
                                "detail": f"期望截断，但结果仅 {truncated.row_count} 行",
                            }
                        )
                elif status == "empty" and not _result_is_empty(execution):
                    problems.append(
                        {
                            "id": item_id,
                            "kind": "gold_status",
                            "detail": f"期望空结果，实际 {execution.row_count} 行",
                        }
                    )
                elif status == "ok" and _result_is_empty(execution):
                    problems.append(
                        {
                            "id": item_id,
                            "kind": "gold_status",
                            "detail": "期望非空结果，实际为空",
                        }
                    )

        if item["split"] == "security":
            attack = item.get("attack_sql") or ""
            outcome = validator.validate(attack)
            expected = set(item.get("expected_codes") or [])
            if outcome.ok or outcome.code not in expected:
                problems.append(
                    {
                        "id": item_id,
                        "kind": "security",
                        "detail": f"期望 {sorted(expected)}，实际 {outcome.code}: {outcome.error}",
                    }
                )

        if item["split"] == "intent":
            outcome = gate.rule_classify(item["question"])
            expected = set(item.get("expected_codes") or [])
            if expected:
                if outcome.code not in expected:
                    problems.append(
                        {
                            "id": item_id,
                            "kind": "intent",
                            "detail": f"期望 {sorted(expected)}，实际 {outcome.code}",
                        }
                    )
            elif not outcome.is_analytics:
                problems.append(
                    {
                        "id": item_id,
                        "kind": "intent",
                        "detail": f"期望 analytics，实际 {outcome.intent}({outcome.code})",
                    }
                )

    if verbose:
        if problems:
            print(f"gold 校验失败 {len(problems)} 项:")
            for problem in problems:
                print(f"  - {problem['id']} [{problem['kind']}] {problem['detail']}")
        else:
            print(f"gold 校验通过: {len(items)} 条")
    return problems


def _result_is_empty(execution: ExecutionResult) -> bool:
    if execution.row_count == 0:
        return True
    return bool(
        execution.row_count == 1
        and len(execution.columns) == 1
        and execution.rows[0][0] == 0
    )


# ---------------------------------------------------------------- comparison


def _normalize_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def value_matches(left: Any, right: Any, tol_abs: float, tol_rel: float) -> bool:
    left = _normalize_value(left)
    right = _normalize_value(right)
    if left is None or right is None:
        return left is None and right is None
    if _is_number(left) and _is_number(right):
        if not (math.isfinite(float(left)) and math.isfinite(float(right))):
            return float(left) == float(right)
        diff = abs(float(left) - float(right))
        if diff <= tol_abs:
            return True
        scale = max(abs(float(left)), abs(float(right)))
        return scale > 0 and diff / scale <= tol_rel
    return str(left).strip() == str(right).strip()


def row_matches(
    left: tuple[Any, ...],
    right: tuple[Any, ...],
    tol_abs: float,
    tol_rel: float,
) -> bool:
    return len(left) == len(right) and all(
        value_matches(a, b, tol_abs, tol_rel) for a, b in zip(left, right)
    )


def rows_match(
    agent_rows: list[tuple[Any, ...]],
    gold_rows: list[tuple[Any, ...]],
    *,
    ordered: bool,
    tol_abs: float,
    tol_rel: float,
) -> bool:
    if len(agent_rows) != len(gold_rows):
        return False
    if ordered:
        if all(
            row_matches(a, b, tol_abs, tol_rel)
            for a, b in zip(agent_rows, gold_rows)
        ):
            return True
        return ordered_tie_match(agent_rows, gold_rows, tol_abs, tol_rel)
    used = [False] * len(gold_rows)
    for agent_row in agent_rows:
        for index, gold_row in enumerate(gold_rows):
            if not used[index] and row_matches(agent_row, gold_row, tol_abs, tol_rel):
                used[index] = True
                break
        else:
            return False
    return True


def _multiset_match(
    left: list[tuple[Any, ...]],
    right: list[tuple[Any, ...]],
    tol_abs: float,
    tol_rel: float,
) -> bool:
    if len(left) != len(right):
        return False
    used = [False] * len(right)
    for row in left:
        for index, other in enumerate(right):
            if not used[index] and row_matches(row, other, tol_abs, tol_rel):
                used[index] = True
                break
        else:
            return False
    return True


def _dateish(value: str) -> bool:
    text = value.strip()
    return bool(
        re.match(r"^\d{4}(?:[-/]\d{1,2}){0,2}", text)
        or re.match(r"^\d{4}[-/]?Q[1-4]$", text, re.I)
    )


def ordered_tie_match(
    agent_rows: list[tuple[Any, ...]],
    gold_rows: list[tuple[Any, ...]],
    tol_abs: float,
    tol_rel: float,
) -> bool:
    """允许同一排序键（并列值）内部顺序不同，但不允许跨并列组乱序。"""
    if not gold_rows or len(agent_rows) != len(gold_rows):
        return False
    width = len(gold_rows[0])
    key_columns: list[int] = []
    for index in range(width):
        values = [row[index] for row in gold_rows]
        if not all(
            _is_number(value)
            or (isinstance(value, str) and _dateish(value))
            for value in values
        ):
            continue
        direction = 0
        monotonic = True
        for previous, current in zip(values, values[1:]):
            if value_matches(previous, current, tol_abs, tol_rel):
                continue
            try:
                order = 1 if float(current) >= float(previous) else -1
            except (TypeError, ValueError):
                order = 1 if str(current) >= str(previous) else -1
            if direction == 0:
                direction = order
            elif direction != order:
                monotonic = False
                break
        if monotonic and direction != 0:
            key_columns.append(index)
    if not key_columns:
        return False

    groups: list[tuple[int, int]] = []
    start = 0
    for position in range(1, len(gold_rows)):
        same = all(
            value_matches(
                gold_rows[position][index],
                gold_rows[start][index],
                tol_abs,
                tol_rel,
            )
            for index in key_columns
        )
        if not same:
            groups.append((start, position))
            start = position
    groups.append((start, len(gold_rows)))

    for start, end in groups:
        if not _multiset_match(
            agent_rows[start:end], gold_rows[start:end], tol_abs, tol_rel
        ):
            return False
    return True


def rows_prefix_subset(
    agent_rows: list[tuple[Any, ...]],
    gold_rows: list[tuple[Any, ...]],
    *,
    ordered: bool,
    tol_abs: float,
    tol_rel: float,
) -> bool:
    if not agent_rows:
        return False
    if ordered:
        if len(agent_rows) > len(gold_rows):
            return False
        return all(
            row_matches(a, b, tol_abs, tol_rel)
            for a, b in zip(agent_rows, gold_rows)
        )
    used = [False] * len(gold_rows)
    for agent_row in agent_rows:
        for index, gold_row in enumerate(gold_rows):
            if not used[index] and row_matches(agent_row, gold_row, tol_abs, tol_rel):
                used[index] = True
                break
        else:
            return False
    return True


def columns_ok(item: dict[str, Any], columns: list[str]) -> bool:
    names = [column.lower() for column in columns]
    if item.get("strict_columns"):
        expected = {column.lower() for column in item.get("required_columns") or []}
        if expected and set(names) != expected:
            return False
    required = [column.lower() for column in item.get("required_columns") or []]
    return all(column in names for column in required)


def sql_tables(sql: str) -> set[str]:
    if not sql or parse is None:
        return set()
    try:
        statements = [statement for statement in parse(sql, read="sqlite") if statement]
    except Exception:  # noqa: BLE001 - 解析失败按无表处理
        return set()
    ctes = {
        (cte.alias_or_name or "").lower()
        for statement in statements
        for cte in statement.find_all(exp.CTE)
    }
    return {
        table.name.lower()
        for statement in statements
        for table in statement.find_all(exp.Table)
        if table.name and table.name.lower() not in ctes
    }


def _base_column(name: str) -> str:
    base = name.lower().strip()
    for suffix in ("_id", "_name", "_count", "_total", "_amount", "_seconds"):
        if base.endswith(suffix) and len(base) > len(suffix):
            return base[: -len(suffix)]
    return base


def _column_kind(name: str) -> str:
    lower = name.lower()
    if lower.endswith("_id") or lower.endswith("id"):
        return "id"
    if lower.endswith("_name") or lower.endswith("name"):
        return "name"
    return ""


def column_mapping(
    agent_columns: list[str], gold_columns: list[str]
) -> list[int] | None:
    agent = [column.lower() for column in agent_columns]
    gold = [column.lower() for column in gold_columns]
    if len(agent) == len(gold):
        exact = {name: index for index, name in enumerate(agent)}
        if all(name in exact for name in gold) and len(set(gold)) == len(gold):
            return [exact[name] for name in gold]
        return list(range(len(gold)))

    used: set[int] = set()
    mapping: list[int] = []
    for name in gold:
        index = None
        if name in agent:
            candidate = agent.index(name)
            if candidate not in used:
                index = candidate
        if index is None:
            base = _base_column(name)
            kind = _column_kind(name)
            candidates = [
                position
                for position, column in enumerate(agent)
                if position not in used
                and (
                    _base_column(column) == base
                    or base in column
                    or column in name
                )
            ]
            if len(candidates) > 1:
                if kind:
                    preferred = [
                        position
                        for position in candidates
                        if _column_kind(agent[position]) == kind
                    ]
                else:
                    preferred = [
                        position
                        for position in candidates
                        if _column_kind(agent[position]) != "id"
                    ]
                if preferred:
                    candidates = preferred
            if len(candidates) == 1:
                index = candidates[0]
        if index is None:
            return None
        used.add(index)
        mapping.append(index)
    return mapping


def project_rows(
    rows: list[tuple[Any, ...]], mapping: list[int]
) -> list[tuple[Any, ...]]:
    return [tuple(row[index] for index in mapping) for row in rows]


def metric_ok(item: dict[str, Any], result: Any) -> bool:
    expected = item.get("expected_metric")
    if not expected:
        return True
    metric = getattr(result, "metric", None)
    if not metric:
        return False
    family = metric.get("variant_of") or metric.get("id")
    return family == expected or metric.get("id") == expected


# ---------------------------------------------------------------- selection


def selection_scores(
    item: dict[str, Any],
    result: Any,
    used_tables: set[str] | None = None,
) -> tuple[float, float, dict[str, bool]]:
    required = {name.lower() for name in item.get("required_tables") or []}
    allowed_extra = {name.lower() for name in item.get("allowed_extra_tables") or []}
    forbidden = {name.lower() for name in item.get("forbidden_tables") or []}
    selected = {name.lower() for name in (getattr(result, "tables", None) or [])}
    selection = getattr(result, "selection", None) or {}
    selected_fallback = bool(selection.get("fallback_all", False))

    touched = used_tables if used_tables else selected
    touched_forbidden = bool(touched & forbidden)
    recall = len(required & selected) / len(required) if required else 1.0
    denominator = selected - allowed_extra
    precision = len(required & selected) / len(denominator) if denominator else 1.0
    flags = {"forbidden": touched_forbidden, "fallback_all": selected_fallback}
    return precision, recall, flags


# ---------------------------------------------------------------- run evaluation


@dataclass
class CaseRun:
    correct: bool
    status: str = ""
    error_code: str = ""
    empty_reason: str | None = None
    metric: str | None = None
    tables: list[str] = field(default_factory=list)
    precision: float = 1.0
    recall: float = 1.0
    fallback_all: bool = False
    forbidden: bool = False
    grounding_ok: bool = True
    used_fallback: bool = False
    elapsed_ms: float = 0.0
    detail: str = ""
    answer: str = ""
    sql: str = ""


@dataclass
class CaseResult:
    item: dict[str, Any]
    runs: list[CaseRun] = field(default_factory=list)

    @property
    def correct(self) -> bool:
        if not self.runs:
            return False
        return sum(1 for run in self.runs if run.correct) * 2 >= len(self.runs)


def evaluate_agent_case(
    item: dict[str, Any],
    result: Any,
    gold_rows: list[tuple[Any, ...]],
    gold_columns: list[str],
    *,
    truncation_case: bool,
) -> CaseRun:
    tol_abs = float(item.get("tolerance_abs", 0.01))
    tol_rel = float(item.get("tolerance_rel", 0.001))
    expected_status = item.get("expected_status")
    expected_codes = set(item.get("expected_codes") or [])
    agent_rows = [tuple(row) for row in (result.rows or [])]
    status = getattr(result, "status", "") or ""
    error_code = getattr(result, "error_code", "") or ""

    used_tables = sql_tables(result.sql or "")
    precision, recall, flags = selection_scores(item, result, used_tables)
    metric_pass = metric_ok(item, result)
    relevant = bool(item.get("required_tables"))
    mapping = column_mapping(list(result.columns or []), gold_columns)
    column_pass = mapping is not None and columns_ok(
        item, list(result.columns or [])
    )
    if mapping is not None:
        agent_rows = project_rows(agent_rows, mapping)

    if expected_status == "error":
        return CaseRun(
            correct=error_code in expected_codes,
            status=status,
            error_code=error_code,
            metric=(result.metric or {}).get("id") if result.metric else None,
            tables=list(result.tables or []),
            precision=precision,
            recall=recall,
            fallback_all=flags["fallback_all"],
            forbidden=flags["forbidden"],
            grounding_ok=bool(getattr(result, "grounding_ok", True)),
            used_fallback=bool(getattr(result, "used_fallback", False)),
            elapsed_ms=float(getattr(result, "total_ms", 0.0)),
            detail=f"期望错误码 {sorted(expected_codes)}，实际 {error_code}",
            answer=result.answer,
            sql=result.sql or "",
        )

    if expected_status == "empty":
        reason = getattr(result, "empty_reason", None)
        expected_reason = item.get("expected_empty_reason")
        correct = status == "empty" and (
            not expected_reason or reason == expected_reason
        )
        return CaseRun(
            correct=correct,
            status=status,
            error_code=error_code,
            empty_reason=reason,
            metric=(result.metric or {}).get("id") if result.metric else None,
            tables=list(result.tables or []),
            precision=precision,
            recall=recall,
            fallback_all=flags["fallback_all"],
            forbidden=flags["forbidden"],
            grounding_ok=bool(getattr(result, "grounding_ok", True)),
            used_fallback=bool(getattr(result, "used_fallback", False)),
            elapsed_ms=float(getattr(result, "total_ms", 0.0)),
            detail=f"期望 empty/{expected_reason or '任意'}，实际 {status}/{reason}",
            answer=result.answer,
            sql=result.sql or "",
        )

    if expected_status == "truncated":
        correct = (
            status == "truncated"
            and bool(getattr(result, "truncated", False))
            and column_pass
            and rows_prefix_subset(
                agent_rows,
                gold_rows,
                ordered=bool(item.get("ordered")),
                tol_abs=tol_abs,
                tol_rel=tol_rel,
            )
        )
        return CaseRun(
            correct=correct,
            status=status,
            error_code=error_code,
            metric=(result.metric or {}).get("id") if result.metric else None,
            tables=list(result.tables or []),
            precision=precision,
            recall=recall,
            fallback_all=flags["fallback_all"],
            forbidden=flags["forbidden"],
            grounding_ok=bool(getattr(result, "grounding_ok", True)),
            used_fallback=bool(getattr(result, "used_fallback", False)),
            elapsed_ms=float(getattr(result, "total_ms", 0.0)),
            detail=f"期望 truncated，实际 {status}, rows={result.row_count}",
            answer=result.answer,
            sql=result.sql or "",
        )

    gold_empty = not gold_rows or (
        len(gold_rows) == 1
        and len(gold_columns) == 1
        and gold_rows[0][0] == 0
    )
    if expected_status == "ok":
        status_pass = status in {"ok", "truncated"}
    elif gold_empty:
        status_pass = status == "empty" or (
            status in {"ok", "truncated"}
            and not agent_rows
        )
    else:
        status_pass = status in {"ok", "truncated"}

    rows_pass = False
    if status in {"ok", "truncated"} and not gold_empty:
        rows_pass = column_pass and rows_match(
            agent_rows,
            gold_rows,
            ordered=bool(item.get("ordered")),
            tol_abs=tol_abs,
            tol_rel=tol_rel,
        )
    elif gold_empty and status in {"ok", "truncated"}:
        rows_pass = not agent_rows or rows_match(
            agent_rows,
            gold_rows,
            ordered=bool(item.get("ordered")),
            tol_abs=tol_abs,
            tol_rel=tol_rel,
        )
    elif gold_empty and status == "empty":
        rows_pass = True

    correct = (
        status_pass
        and rows_pass
        and metric_pass
        and not flags["forbidden"]
    )
    detail = ""
    if not status_pass:
        detail = f"状态不符: 期望结果，实际 {status}"
    elif not rows_pass:
        detail = f"结果不符: agent={len(agent_rows)} 行, gold={len(gold_rows)} 行"
    elif not metric_pass:
        detail = f"指标口径不符: 期望 {item.get('expected_metric')}"
    elif flags["forbidden"]:
        detail = "选表命中禁止表"
    return CaseRun(
        correct=correct,
        status=status,
        error_code=error_code,
        metric=(result.metric or {}).get("id") if result.metric else None,
        tables=list(result.tables or []),
        precision=precision if relevant else 1.0,
        recall=recall if relevant else 1.0,
        fallback_all=flags["fallback_all"],
        forbidden=flags["forbidden"],
        grounding_ok=bool(getattr(result, "grounding_ok", True)),
        used_fallback=bool(getattr(result, "used_fallback", False)),
        elapsed_ms=float(getattr(result, "total_ms", 0.0)),
        detail=detail,
        answer=result.answer,
        sql=result.sql or "",
    )


def run_case(
    item: dict[str, Any],
    agents: dict[str, SQLAgent],
    gold_executor: SQLExecutor,
) -> CaseResult:
    case = CaseResult(item=item)
    split = item["split"]
    runs = item.get("_runs", 1)

    if split == "security":
        validator = SQLValidator(agents["catalog"])
        outcome = validator.validate(item.get("attack_sql") or "")
        expected = set(item.get("expected_codes") or [])
        for _ in range(runs):
            case.runs.append(
                CaseRun(
                    correct=(not outcome.ok and outcome.code in expected),
                    status="validated" if outcome.ok else outcome.stage,
                    error_code=outcome.code,
                    detail=f"期望 {sorted(expected)}，实际 {outcome.code}",
                )
            )
        return case

    if split == "intent":
        gate = IntentGate()
        outcome = gate.rule_classify(item["question"])
        expected = set(item.get("expected_codes") or [])
        if expected:
            correct = outcome.code in expected
            detail = f"期望 {sorted(expected)}，实际 {outcome.code}"
        else:
            correct = outcome.is_analytics
            detail = f"期望 analytics，实际 {outcome.intent}({outcome.code})"
        for _ in range(runs):
            case.runs.append(
                CaseRun(
                    correct=correct,
                    status=outcome.intent,
                    error_code=outcome.code,
                    detail=detail,
                )
            )
        return case

    gold = gold_executor.execute(item.get("gold_sql") or "")
    if not gold.ok:
        case.runs.append(
            CaseRun(
                correct=False,
                status="gold_error",
                error_code=gold.error_code,
                detail=f"gold SQL 执行失败: {gold.error}",
            )
        )
        return case

    truncation_case = item.get("expected_status") == "truncated"
    agent = agents["truncation"] if truncation_case else agents["main"]
    for _ in range(runs):
        try:
            result = agent.run(item["question"])
            case.runs.append(
                evaluate_agent_case(
                    item,
                    result,
                    [tuple(row) for row in gold.rows],
                    list(gold.columns),
                    truncation_case=truncation_case,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 单题异常不应中断评测
            case.runs.append(
                CaseRun(
                    correct=False,
                    status="exception",
                    error_code=getattr(exc, "code", ""),
                    detail=f"{type(exc).__name__}: {exc}",
                    sql=traceback.format_exc(limit=2),
                )
            )
    return case


# ---------------------------------------------------------------- statistics


def wilson_interval(success: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    p = success / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def summarize_cases(cases: list[CaseResult], runs: int) -> dict[str, Any]:
    total = len(cases)
    correct = sum(1 for case in cases if case.correct)
    low, high = wilson_interval(correct, total)
    per_run: list[int] = []
    for index in range(runs):
        per_run.append(
            sum(
                1
                for case in cases
                if len(case.runs) > index and case.runs[index].correct
            )
        )
    return {
        "cases": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "accuracy_ci95": [round(low, 4), round(high, 4)],
        "run_correct": per_run,
        "run_accuracy": [
            round(count / total, 4) if total else 0.0 for count in per_run
        ],
        "median_run_correct": statistics.median(per_run) if per_run else 0,
        "run_span": (max(per_run) - min(per_run)) if per_run else 0,
    }


def selection_summary(cases: list[CaseResult]) -> dict[str, Any]:
    rows: list[CaseRun] = []
    fallback_rows: list[CaseRun] = []
    for case in cases:
        if not case.item.get("required_tables"):
            continue
        if case.item.get("expected_status") == "error":
            # 意图/指标被拒答的题不进入选表统计
            continue
        for run in case.runs:
            rows.append(run)
            if run.fallback_all:
                fallback_rows.append(run)
    return {
        "cases": len(rows),
        "precision": round(mean(run.precision for run in rows), 4) if rows else 1.0,
        "recall": round(mean(run.recall for run in rows), 4) if rows else 1.0,
        "forbidden_hits": sum(1 for run in rows if run.forbidden),
        "fallback_cases": len(fallback_rows),
        "fallback_precision": (
            round(mean(run.precision for run in fallback_rows), 4)
            if fallback_rows
            else None
        ),
    }


def grounding_summary(cases: list[CaseResult]) -> dict[str, Any]:
    analytics = [case for case in cases if case.item["split"] in ANALYTICS_SPLITS]
    total = 0
    passed = 0
    fallback = 0
    failures: list[str] = []
    for case in analytics:
        for run in case.runs:
            if run.status in {"ok", "truncated", "empty"} or not run.status:
                total += 1
                if run.grounding_ok:
                    passed += 1
                elif run.used_fallback:
                    # 模型汇总未通过校验，但已回退到由结果行生成的模板答案
                    passed += 1
                    fallback += 1
                else:
                    failures.append(case.item["id"])
                    fallback += int(run.used_fallback)
    return {
        "cases": total,
        "passed": passed,
        "rate": round(passed / total, 4) if total else 1.0,
        "fallback": fallback,
        "failures": failures,
    }


def tag_summary(cases: list[CaseResult], split: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    selected = cases if split is None else [
        case for case in cases if case.item["split"] == split
    ]
    tags: set[str] = set()
    for case in selected:
        tags.update(case.item.get("tags") or [])
    for tag in sorted(tags):
        tagged = [
            case for case in selected if tag in (case.item.get("tags") or [])
        ]
        correct = sum(1 for case in tagged if case.correct)
        result[tag] = {
            "cases": len(tagged),
            "correct": correct,
            "accuracy": round(correct / len(tagged), 4) if tagged else 0.0,
        }
    return result


# ---------------------------------------------------------------- gates


def compute_gates(
    cases: list[CaseResult],
    summary: dict[str, Any],
    selection: dict[str, Any],
    grounding: dict[str, Any],
    baseline: dict[str, Any] | None,
    runs: int,
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    splits = {
        split: [case for case in cases if case.item["split"] == split]
        for split in ANALYTICS_SPLITS
    }

    def add(name: str, ok: bool, detail: str) -> None:
        gates.append({"name": name, "ok": bool(ok), "detail": detail})

    holdout = summarize_cases(splits.get("holdout", []), runs)
    dev = summarize_cases(splits.get("dev", []), runs)
    if holdout["cases"]:
        add(
            "holdout_total",
            holdout["median_run_correct"] >= GATES["holdout_total"],
            f"{holdout['median_run_correct']}/{holdout['cases']} (门槛 {GATES['holdout_total']})",
        )
    if dev["cases"]:
        add(
            "dev_total",
            dev["median_run_correct"] >= GATES["dev_total"],
            f"{dev['median_run_correct']}/{dev['cases']} (门槛 {GATES['dev_total']})",
        )

    holdout_tags = tag_summary(splits.get("holdout", []), "holdout")
    core_failures: list[str] = []
    for tag in CORE_TAGS:
        stats = holdout_tags.get(tag)
        if not stats:
            continue
        ok = stats["correct"] >= GATES["core_tag_pass"]
        if not ok:
            core_failures.append(tag)
        add(
            f"core_tag:{tag}",
            ok,
            f"{stats['correct']}/{stats['cases']} (门槛 {GATES['core_tag_pass']}/{GATES['core_tag_size']})",
        )
    if holdout["cases"]:
        add("core_tag_failures", len(core_failures) < 2, f"失败标签数 {len(core_failures)}")

    analytics_cases = [case for case in cases if case.item["split"] in ANALYTICS_SPLITS]
    if analytics_cases:
        add(
            "selection_precision",
            selection["precision"] >= GATES["selection_precision"],
            f"{selection['precision']:.1%} (门槛 {GATES['selection_precision']:.0%})",
        )
        add(
            "selection_recall",
            selection["recall"] >= GATES["selection_recall"],
            f"{selection['recall']:.1%} (门槛 {GATES['selection_recall']:.0%})",
        )
        if selection["fallback_precision"] is not None:
            add(
                "fallback_precision",
                selection["fallback_precision"] < GATES["fallback_precision"]
                or selection["fallback_cases"] == 0,
                f"{selection['fallback_precision']:.1%} (应低于 {GATES['fallback_precision']:.0%})",
            )
        add(
            "grounding_100",
            grounding["rate"] >= 1.0,
            f"{grounding['rate']:.1%}",
        )

    security_cases = [case for case in cases if case.item["split"] == "security"]
    security_ok = all(case.correct for case in security_cases)
    if security_cases:
        add(
            "security_redline",
            security_ok,
            f"{sum(1 for case in security_cases if case.correct)}/{len(security_cases)}",
        )

    if holdout["cases"]:
        add(
            "holdout_stability",
            holdout["run_span"] <= GATES["stability_span"],
            f"波动 {holdout['run_span']} 题 (上限 {GATES['stability_span']})",
        )

    if baseline:
        baseline_cases = baseline.get("case_correct") or {}
        dropped = sum(
            1
            for case in cases
            if baseline_cases.get(case.item["id"]) and not case.correct
        )
        add(
            "regression_correct_drop",
            dropped < 2,
            f"较基线少正确 {dropped} 题 (阻断阈值 2)",
        )
        baseline_tags = baseline.get("holdout_tags") or {}
        regressed_tags = [
            tag
            for tag in CORE_TAGS
            if tag in baseline_tags
            and tag in holdout_tags
            and holdout_tags[tag]["correct"] < baseline_tags[tag]["correct"] - 1
        ]
        add(
            "regression_core_tags",
            len(regressed_tags) < 2,
            f"核心标签回退 {len(regressed_tags)} 个: {', '.join(regressed_tags) or '-'}",
        )
        if security_cases:
            add(
                "regression_redline",
                security_ok,
                "安全红线" if not security_ok else "安全红线未回退",
            )
    return gates


# ---------------------------------------------------------------- reporting


def build_baseline(
    cases: list[CaseResult],
    summary: dict[str, Any],
    selection: dict[str, Any],
    grounding: dict[str, Any],
    fingerprint: dict[str, Any],
) -> dict[str, Any]:
    split_summary = {
        split: summarize_cases(
            [case for case in cases if case.item["split"] == split],
            max((len(case.runs) for case in cases), default=1),
        )
        for split in {case.item["split"] for case in cases}
    }
    return {
        "version": 1,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "fingerprint": fingerprint,
        "summary": summary,
        "splits": split_summary,
        "selection": selection,
        "grounding": grounding,
        "holdout_tags": tag_summary(
            [case for case in cases if case.item["split"] == "holdout"], "holdout"
        ),
        "case_correct": {case.item["id"]: case.correct for case in cases},
    }


def fingerprint(settings: Settings, gold_path: Path, catalog: DBCatalog) -> dict[str, Any]:
    gold_hash = hashlib.sha256(gold_path.read_bytes()).hexdigest()[:16]
    return {
        "app_version": settings.app_version,
        "prompt_version": settings.prompt_version,
        "model": settings.model,
        "catalog": getattr(catalog, "fingerprint", ""),
        "gold_sha256": gold_hash,
    }


def print_summary(
    summary: dict[str, Any],
    splits: dict[str, Any],
    selection: dict[str, Any],
    grounding: dict[str, Any],
    gates: list[dict[str, Any]],
) -> None:
    print("\n" + "=" * 64)
    print(
        f"总体准确率 : {summary['correct']}/{summary['cases']} "
        f"({summary['accuracy']:.1%}) CI95=[{summary['accuracy_ci95'][0]:.1%}, "
        f"{summary['accuracy_ci95'][1]:.1%}]"
    )
    print(f"各次运行   : {summary['run_correct']} 中位数={summary['median_run_correct']}")
    for split, stats in splits.items():
        print(
            f"  {split:<9}: {stats['correct']}/{stats['cases']} "
            f"({stats['accuracy']:.1%}) runs={stats['run_correct']}"
        )
    print(
        f"选表       : precision={selection['precision']:.1%} "
        f"recall={selection['recall']:.1%} "
        f"fallback={selection['fallback_cases']} "
        f"(precision={selection['fallback_precision']})"
    )
    print(
        f"grounding  : {grounding['passed']}/{grounding['cases']} "
        f"({grounding['rate']:.1%}) 模板回退={grounding['fallback']}"
    )
    print("gate:")
    for gate in gates:
        print(f"  [{'PASS' if gate['ok'] else 'FAIL'}] {gate['name']}: {gate['detail']}")


def case_detail(case: CaseResult) -> dict[str, Any]:
    item = case.item
    runs = case.runs
    first = runs[0] if runs else CaseRun(correct=False)
    return {
        "id": item["id"],
        "split": item["split"],
        "question": item["question"],
        "tags": item.get("tags") or [],
        "correct": case.correct,
        "run_correct": [run.correct for run in runs],
        "status": first.status,
        "error_code": first.error_code,
        "empty_reason": first.empty_reason,
        "metric": first.metric,
        "expected_metric": item.get("expected_metric"),
        "selected_tables": first.tables,
        "required_tables": item.get("required_tables") or [],
        "selection_precision": round(first.precision, 4),
        "selection_recall": round(first.recall, 4),
        "fallback_all": first.fallback_all,
        "grounding_ok": first.grounding_ok,
        "used_fallback": first.used_fallback,
        "elapsed_ms": round(first.elapsed_ms, 1),
        "detail": first.detail,
        "sql": first.sql,
        "answer": first.answer,
        "runs": [
            {
                "correct": run.correct,
                "status": run.status,
                "error_code": run.error_code,
                "detail": run.detail,
            }
            for run in runs
        ],
    }


# ---------------------------------------------------------------- main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SQL Agent 评测（130 题）")
    parser.add_argument("--gold", default=str(Path(__file__).parent / "gold.jsonl"))
    parser.add_argument(
        "--output", default=str(Path(__file__).parent / "results.json")
    )
    parser.add_argument(
        "--split",
        default="",
        help="逗号分隔: dev,holdout,boundary,security,entity,intent；空为全部",
    )
    parser.add_argument("--id", default="", help="逗号分隔的题目 id")
    parser.add_argument("--limit", type=int, default=0, help="限制题目数量")
    parser.add_argument(
        "--runs", type=int, default=0, help="每题运行次数（默认取配置 eval_runs）"
    )
    parser.add_argument(
        "--verify-gold", action="store_true", help="只做 gold/安全/意图离线校验"
    )
    parser.add_argument(
        "--save-baseline",
        nargs="?",
        const=str(Path(__file__).parent / "baseline.json"),
        default=None,
        help="保存本次结果作为回归基线",
    )
    parser.add_argument(
        "--baseline",
        default=str(Path(__file__).parent / "baseline.json"),
        help="回归基线路径（存在则自动比较）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)

    gold_path = Path(args.gold)
    items = load_gold(gold_path)
    splits = {part.strip() for part in args.split.split(",") if part.strip()} or None
    ids = {part.strip() for part in args.id.split(",") if part.strip()} or None
    selected = filter_items(items, splits, ids, args.limit)
    if not selected:
        print("没有可评测的样例")
        return 1

    settings = load_settings()
    catalog = DBCatalog(settings.db_path).load()

    problems = verify_gold(selected, catalog)
    if args.verify_gold:
        return 0 if not problems else 1

    if problems:
        print("gold 校验未通过，停止评测。")
        return 1

    agent_settings = replace(settings, max_rows=EVAL_MAX_ROWS)
    trunc_settings = replace(settings, max_rows=TRUNC_MAX_ROWS)
    main_agent = SQLAgent(settings=agent_settings, catalog=catalog)
    trunc_agent = SQLAgent(settings=trunc_settings, catalog=catalog)
    agents = {
        "main": main_agent,
        "truncation": trunc_agent,
        "catalog": catalog,
    }
    gold_executor = SQLExecutor(settings.db_path, max_rows=100_000, timeout_s=30)

    runs = args.runs or max(1, int(settings.eval_runs))
    cases: list[CaseResult] = []
    for index, item in enumerate(selected, start=1):
        item = dict(item, _runs=runs)
        case = run_case(item, agents, gold_executor)
        cases.append(case)
        mark = "PASS" if case.correct else "FAIL"
        first = case.runs[0] if case.runs else CaseRun(False)
        print(
            f"[{index:03d}/{len(selected):03d}] {mark} {item['id']:<4} "
            f"{first.status or '-':<10} {first.elapsed_ms:7.0f}ms "
            f"| {item['question'][:32]}"
        )
        if not case.correct and first.detail:
            print(f"    {first.detail}")
            if first.sql and first.status == "exception":
                print(f"    {first.sql}")

    summary = summarize_cases(cases, runs)
    split_summary = {
        split: summarize_cases(
            [case for case in cases if case.item["split"] == split], runs
        )
        for split in sorted({case.item["split"] for case in cases})
    }
    selection = selection_summary(cases)
    grounding = grounding_summary(cases)
    tags = {
        split: tag_summary(
            [case for case in cases if case.item["split"] == split], split
        )
        for split in sorted({case.item["split"] for case in cases})
    }

    baseline = None
    baseline_path = Path(args.baseline)
    if baseline_path.exists() and not args.save_baseline:
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            baseline = None

    fp = fingerprint(settings, gold_path, catalog)
    gates = compute_gates(cases, summary, selection, grounding, baseline, runs)
    passed = all(gate["ok"] for gate in gates)

    print_summary(summary, split_summary, selection, grounding, gates)
    print(f"\n评测结论: {'PASS' if passed else 'BLOCK'}")

    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "fingerprint": fp,
        "config": {
            "runs": runs,
            "eval_max_rows": EVAL_MAX_ROWS,
            "truncation_max_rows": TRUNC_MAX_ROWS,
            "splits": sorted({case.item["split"] for case in cases}),
        },
        "summary": summary,
        "splits": split_summary,
        "tags": tags,
        "selection": selection,
        "grounding": grounding,
        "gates": gates,
        "passed": passed,
        "baseline_compared": baseline is not None,
        "details": [case_detail(case) for case in cases],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"详细结果已保存: {output}")

    if args.save_baseline:
        if not passed:
            print("评测未通过，跳过基线保存（避免把失败的快照当作回归基线）。")
        else:
            baseline_report = build_baseline(
                cases, summary, selection, grounding, fp
            )
            baseline_report["passed"] = passed
            baseline_path = Path(args.save_baseline)
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                json.dumps(baseline_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"基线已保存: {baseline_path}")

    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
