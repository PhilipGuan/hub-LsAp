"""SQLite 只读访问：SQLAlchemy 解析 schema 和校验，sqlite3 执行（带超时）。"""

import sqlite3
import time
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

r"""
__file__：当前脚本自己的路径字符串，比如 E:\...\homework2\db.py
Path(__file__)：转成 Path 对象，才能用路径方法
.with_name("chinook.db")：把文件名换掉，目录保持不变
"""
DB_PATH = Path(__file__).with_name("chinook.db")  # 返回Path对象
MAX_ROWS = 200
TIMEOUT = 5.0

_engine = None
_schema = None


def get_engine():
    """只读引擎，进程内复用。"""
    global _engine
    if _engine is None:
        url = "sqlite:///file:" + DB_PATH.as_posix() + "?mode=ro&uri=true"
        _engine = create_engine(url)
    return _engine


"""
load_schema最终返回：
{
  "tables": ["albums", "artists", "customers", "employees", "genres",
             "invoice_items", "invoices", "media_types", "playlist_track",
             "playlists", "tracks"],

  "meta": {
    "albums": {
      "count": 347,
      "columns": [
        ("AlbumId",  "INTEGER",       True),
        ("Title",    "NVARCHAR(160)", False),
        ("ArtistId", "INTEGER",       False),
      ],
      "samples": [
        ["1", "For Those About To Rock We Salute You", "1"],
        ["2", "Balls to the Wall", "2"],
      ],
    },
    "artists":  { "count": 275, "columns": [...], "samples": [...] },
    "customers": { "count": 59,  "columns": [...], "samples": [...] },
    ... 共 11 个键
  },

  "edges": [
    ("albums",    "ArtistId",     "artists",   "ArtistId"),
    ("customers", "SupportRepId", "employees", "EmployeeId"),
    ("employees", "ReportsTo",    "employees", "EmployeeId"),
    ... 共 11 条
  ],
}
"""
def load_schema():
    """表名、字段、行数、样例、外键，进程内只解析一次。"""
    global _schema
    if _schema:
        return _schema

    engine = get_engine()
    insp = inspect(engine)  # 返回 Inspector 对象，用它读取数据库的表名、列、外键、主键、索引
    tables = sorted(insp.get_table_names())

    meta = {}
    with engine.connect() as conn:
        for t in tables:
            meta[t] = {
                # scalar_one:取「唯一一行里唯一一列」的值
                "count": conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar_one(),
                # get_columns:返回列信息字典的列表
                "columns": [(c["name"], str(c["type"]), bool(c.get("primary_key")))
                            for c in insp.get_columns(t)],
                # row是查到的一行，v是从这一行中取到的某一列的值
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
    lines = []
    for t in s["tables"]:
        # 得到一张表的所有字段名
        cols = ", ".join(c[0] for c in s["meta"][t]["columns"])
        lines.append(f"- {t}（{s['meta'][t]['count']} 行）字段：{cols}")
    lines.append("\n外键关系：")
    lines += [f"- {a}.{b} -> {c}.{d}" for a, b, c, d in s["edges"]]
    return "\n".join(lines)


"""
最终blocks结构为：
### albums（347 行）
| 字段 | 类型 | 主键 |
| --- | --- | --- |
| AlbumId | INTEGER | 是 |
| Title | NVARCHAR(160) | 否 |
| ArtistId | INTEGER | 否 |

样例数据：
| AlbumId | Title | ArtistId |
| --- | --- | --- |
| 1 | For Those About To Rock We Salute You | 1 |
| 2 | Balls to the Wall | 2 |

表之间的外键连接关系：无
"""
def render_schema(tables):
    """给生成 / 改写 Agent 的详细结构：字段表 + 样例 + 连接关系。"""
    s = load_schema()
    blocks = []
    for t in tables:
        info = s["meta"][t]
        blocks.append(f"### {t}（{info['count']} 行）")
        blocks.append("| 字段 | 类型 | 主键 |")
        blocks.append("| --- | --- | --- |")
        for name, ctype, pk in info["columns"]:
            blocks.append(f"| {name} | {ctype} | {'是' if pk else '否'} |")

        names = [c[0] for c in info["columns"]]
        blocks.append("\n样例数据：")
        blocks.append("| " + " | ".join(names) + " |")
        blocks.append("| " + " | ".join("---" for _ in names) + " |")
        for row in info["samples"]:
            blocks.append("| " + " | ".join(v for v in row) + " |")
        blocks.append("")

    picked = set(tables)
    edges = [e for e in s["edges"] if e[0] in picked and e[2] in picked]
    blocks.append("表之间的外键连接关系：" + ("无" if not edges else ""))
    blocks += [f"- {a}.{b} -> {c}.{d}" for a, b, c, d in edges]
    return "\n".join(blocks)


def run_sql(sql):
    """只读执行，截断到 MAX_ROWS，超时 5 秒。SQLAlchemy 拿不到 progress handler，这里用 sqlite3。"""
    conn = sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)
    deadline = time.monotonic() + TIMEOUT  # monotonic用来记录从某个固定点到现在过了多少秒
    try:
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
        cur = conn.execute(sql)  # 返回一个 sqlite3.Cursor（游标）
        columns = [d[0] for d in cur.description]  # description获得列信息,7元组。
        rows = cur.fetchmany(MAX_ROWS + 1)  # 取MAX_ROWS + 1行
    finally:
        conn.close()

    truncated = len(rows) > MAX_ROWS
    return {
        "columns": columns,
        "rows": [["" if v is None else str(v) for v in r] for r in rows[:MAX_ROWS]],
        "row_count": min(len(rows), MAX_ROWS),
        "truncated": truncated,
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
