"""第 4 步：SQL 校验（静态 + EXPLAIN）。"""
import re

from sqlalchemy import text

FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter",
    "create", "replace", "attach", "truncate", "pragma",
)


def static_check(sql: str, parser, tables: list[str] | None = None) -> tuple[bool, str]:
    """不执行 SQL，只做语法/安全/表名检查。"""
    s = (sql or "").strip()
    low = s.lower()

    if not low.startswith("select"):
        return False, "只允许 SELECT 语句"

    for w in FORBIDDEN:
        if re.search(rf"\b{w}\b", low):
            return False, f"包含禁止关键字: {w}"

    # 表名必须在数据库里存在
    known = set(parser.table_names)
    for m in re.finditer(r"\b(?:from|join)\s+([a-zA-Z_][\w]*)", low):
        t = m.group(1)
        if t not in known:
            return False, f"未知表: {t}"

    # 如果做了表选择，检查是否越界（可选）
    if tables:
        allowed = set(tables)
        for m in re.finditer(r"\b(?:from|join)\s+([a-zA-Z_][\w]*)", low):
            t = m.group(1)
            if t not in allowed:
                return False, f"使用了未选中的表: {t}"

    return True, "ok"


def dry_run(sql: str, engine) -> tuple[bool, str]:
    """用 EXPLAIN QUERY PLAN 让 SQLite 编译 SQL，不真正执行。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("EXPLAIN QUERY PLAN " + sql))
        return True, "ok"
    except Exception as e:
        return False, f"EXPLAIN 失败: {e}"


def validate(sql: str, parser, engine, tables: list[str] | None = None) -> dict:
    """静态校验 + EXPLAIN，返回统一结构。"""
    ok, err = static_check(sql, parser, tables)
    if not ok:
        return {"ok": False, "stage": "static", "error": err}

    ok, err = dry_run(sql, engine)
    if not ok:
        return {"ok": False, "stage": "dry_run", "error": err}

    return {"ok": True, "stage": "ok", "error": None}