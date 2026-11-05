"""SQLite 只读访问：SQLAlchemy 解析 schema，sqlite3 执行（带超时）。"""

import sqlite3
import time
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

DB_PATH = Path(__file__).with_name("chinook.db")
MAX_ROWS = 200
TIMEOUT = 5.0

_engine = None
_schema = None


def get_engine():
    """只读引擎，进程内复用。"""
    global _engine
    if _engine is None:
        _engine = create_engine("sqlite:///file:" + DB_PATH.as_posix() + "?mode=ro&uri=true")
    return _engine


def load_schema():
    """表名、字段、行数、样例、外键，进程内只解析一次。"""
    global _schema
    if _schema:
        return _schema

    insp = inspect(get_engine())
    tables = [t for t in sorted(insp.get_table_names()) if not t.startswith("sqlite_")]

    meta = {}
    with get_engine().connect() as conn:
        for t in tables:
            meta[t] = {
                "count": conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar_one(),
                "columns": [(c["name"], str(c["type"]), bool(c.get("primary_key")))
                            for c in insp.get_columns(t)],
                "samples": [["" if v is None else str(v) for v in row]
                            for row in conn.execute(text(f'SELECT * FROM "{t}" LIMIT 2')).all()],
            }

    edges = []
    for t in tables:
        for fk in insp.get_foreign_keys(t):
            edges.append((t, fk["constrained_columns"][0],
                          fk["referred_table"], fk["referred_columns"][0]))

    _schema = {"tables": tables, "meta": meta, "edges": edges}
    return _schema


def render_catalog():
    """给选表 Agent 的轻量目录：表名 + 字段 + 外键。"""
    s = load_schema()
    lines = [f"- {t}（{s['meta'][t]['count']} 行）字段："
             + ", ".join(c[0] for c in s["meta"][t]["columns"])
             for t in s["tables"]]
    lines.append("\n外键关系：")
    lines += [f"- {a}.{b} -> {c}.{d}" for a, b, c, d in s["edges"]]
    return "\n".join(lines)


def render_schema(tables):
    """给生成 / 改写 Agent 的详细结构：字段表 + 样例 + 连接关系。"""
    s = load_schema()
    blocks = []
    for t in tables:
        info = s["meta"][t]
        blocks.append(f"### {t}（{info['count']} 行）")
        blocks.append("| 字段 | 类型 | 主键 |")
        blocks.append("| --- | --- | --- |")
        blocks += [f"| {n} | {c} | {'是' if pk else '否'} |" for n, c, pk in info["columns"]]

        names = [c[0] for c in info["columns"]]
        blocks.append("\n样例数据：")
        blocks.append("| " + " | ".join(names) + " |")
        blocks.append("| " + " | ".join("---" for _ in names) + " |")
        blocks += ["| " + " | ".join(row) + " |" for row in info["samples"]]
        blocks.append("")

    picked = set(tables)
    edges = [e for e in s["edges"] if e[0] in picked and e[2] in picked]
    blocks.append("表之间的外键连接关系：" + ("无" if not edges else ""))
    blocks += [f"- {a}.{b} -> {c}.{d}" for a, b, c, d in edges]
    return "\n".join(blocks)


def run_sql(sql):
    """只读执行，只允许 SELECT / WITH，截断到 MAX_ROWS，超时 5 秒。"""
    head = sql.lstrip("( \n\t").upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError("unsafe: 只允许 SELECT 或 WITH 查询")

    conn = sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)
    deadline = time.monotonic() + TIMEOUT
    try:
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS + 1)
    finally:
        conn.close()

    return {
        "columns": columns,
        "rows": [["" if v is None else str(v) for v in r] for r in rows[:MAX_ROWS]],
        "row_count": min(len(rows), MAX_ROWS),
        "truncated": len(rows) > MAX_ROWS,
    }


def is_connected(tables):
    """所选表能否通过外键连通，避免笛卡尔积。"""
    if len(tables) <= 1:
        return True
    picked = set(tables)
    edges = [(a, c) for a, _, c, _ in load_schema()["edges"] if a in picked and c in picked]
    seen = {tables[0]}
    while True:
        before = len(seen)
        for a, c in edges:
            if a in seen:
                seen.add(c)
            elif c in seen:
                seen.add(a)
        if len(seen) == before:
            break
    return len(seen) == len(picked)
