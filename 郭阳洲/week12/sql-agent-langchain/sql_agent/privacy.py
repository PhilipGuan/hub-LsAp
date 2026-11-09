from __future__ import annotations

import re
from typing import Any, Iterable

try:
    from sqlglot import exp, parse
    HAS_SQLGLOT = True
except ImportError:  # pragma: no cover - sqlglot 是必需依赖
    exp = None
    parse = None
    HAS_SQLGLOT = False

# 文档 03 §11.2 的 MVP 敏感列；表前缀 Billing/Shipping 归一化后再判断。
SENSITIVE_COLUMN_NAMES = frozenset(
    {
        "email",
        "phone",
        "address",
        "company",
        "firstname",
        "lastname",
        "city",
        "state",
        "postalcode",
        "fax",
    }
)

_PREFIXES = ("billing", "shipping", "mailing", "home", "work")


def normalize_column(name: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    for prefix in _PREFIXES:
        if key.startswith(prefix) and len(key) > len(prefix):
            return key[len(prefix) :]
    return key


def is_sensitive_name(name: str) -> bool:
    return normalize_column(name) in SENSITIVE_COLUMN_NAMES


def is_star_projection(node: Any) -> bool:
    if not HAS_SQLGLOT:
        return False
    if isinstance(node, exp.Star):
        return True
    return isinstance(node, exp.Column) and isinstance(node.this, exp.Star)


def _inside_count(column: Any) -> bool:
    parent = column.parent
    while parent is not None:
        if isinstance(parent, exp.Count):
            return True
        if isinstance(parent, (exp.Select, exp.Subquery)):
            return False
        parent = parent.parent
    return False


def _sensitive_columns_in(node: Any) -> list[Any]:
    if not HAS_SQLGLOT:
        return []
    found: list[Any] = []
    for column in node.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            continue
        if is_sensitive_name(column.name) and not _inside_count(column):
            found.append(column)
    return found


def projection_violations(sql: str) -> list[str]:
    """返回 SELECT * / 敏感列投影 / 敏感列分组排序等违规说明。"""
    if not HAS_SQLGLOT:
        return []
    try:
        statements = list(parse(sql, read="sqlite"))
    except Exception:
        return []
    violations: list[str] = []
    seen: set[str] = set()

    def add(message: str) -> None:
        if message not in seen:
            seen.add(message)
            violations.append(message)

    for statement in statements:
        if statement is None:
            continue
        for select in statement.find_all(exp.Select):
            for projection in select.expressions:
                if is_star_projection(projection):
                    add("SELECT * 会暴露全部字段，请明确列出字段")
                    continue
                for column in _sensitive_columns_in(projection):
                    add(f"敏感列不能直接投影: {column.sql(dialect='sqlite')}")

            for clause in ("group", "having", "order"):
                node = select.args.get(clause)
                if node is None:
                    continue
                for column in _sensitive_columns_in(node):
                    add(f"敏感列不能用于分组/排序: {column.sql(dialect='sqlite')}")

            for window in select.find_all(exp.Window):
                for column in _sensitive_columns_in(window):
                    add(f"敏感列不能用于窗口分组/排序: {column.sql(dialect='sqlite')}")
    return violations


def mask_value(column: str, value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    key = normalize_column(column)
    if key == "email" and "@" in text:
        local, domain = text.split("@", 1)
        return (local[:1] or "*") + "***@" + domain
    if key in {"firstname", "lastname"}:
        return (text[:1] + "***") if text else "***"
    if key in {"phone", "fax"}:
        digits = re.sub(r"\D", "", text)
        return "***" + digits[-4:] if len(digits) >= 4 else "***"
    if key == "postalcode":
        return text[:2] + "***" if len(text) > 2 else "***"
    return "***"


def mask_rows(
    columns: Iterable[str], rows: Iterable[Iterable[Any]]
) -> list[tuple[Any, ...]]:
    columns = list(columns)
    sensitive = [is_sensitive_name(column) for column in columns]
    if not any(sensitive):
        return [tuple(row) for row in rows]
    masked: list[tuple[Any, ...]] = []
    for row in rows:
        values = list(row)
        for index, flag in enumerate(sensitive):
            if flag and index < len(values):
                values[index] = mask_value(columns[index], values[index])
        masked.append(tuple(values))
    return masked


def sanitize_log_text(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "***@***", text)
    text = re.sub(r"(?<!\d)(?:\+?\d[\d\s-]{7,}\d)", "***", text)
    return text
