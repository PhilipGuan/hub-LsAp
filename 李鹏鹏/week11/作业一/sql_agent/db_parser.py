"""数据库解析层。

负责：
    1. 读取表 / 字段 / 主键 / 外键 / 行数等元信息；
    2. 抽样统计每个字段的取值分布（唯一值、空值、常见值、min/max）；
    3. 生成给大模型阅读的 schema 文本（Markdown）；
    4. SQL 只读校验与执行。

实现使用 Python 内置 sqlite3，不依赖 SQLAlchemy / pandas。
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

TEXT_TYPE_HINTS = ("CHAR", "CLOB", "TEXT", "VARCHAR", "NVARCHAR")

FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "TRIGGER",
    "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "GRANT", "REVOKE",
)


class DBParserError(RuntimeError):
    """数据库解析/执行异常。"""


def quote_identifier(name: str) -> str:
    """SQLite 标识符转义。"""
    return '"' + str(name).replace('"', '""') + '"'


def normalize_sql(sql: str) -> str:
    """去掉 Markdown 代码围栏、首尾空白和结尾分号。"""
    text = (sql or "").strip()
    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    while text.endswith(";"):
        text = text[:-1].strip()
    return text


def mask_literals_and_comments(sql: str) -> str:
    """把字符串字面量和注释替换为空格，便于做关键字/分号检查。

    这样 ``SELECT ';'`` 或 ``WHERE name = 'delete'`` 不会被误判。
    """
    out: list[str] = []
    i, n = 0, len(sql)

    while i < n:
        ch = sql[i]

        # '...' / "..." / `...`，支持两个连续引号转义
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(" ")
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue

        # [identifier]
        if ch == "[":
            j = sql.find("]", i + 1)
            i = n if j < 0 else j + 1
            out.append(" ")
            continue

        # -- 注释
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i + 2)
            i = n if j < 0 else j
            out.append(" ")
            continue

        # /* 注释 */
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _md_cell(value: Any, max_len: int = 60) -> str:
    """转义 Markdown 表格单元格。"""
    if value is None:
        return "NULL"
    text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    text = text.strip()
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text


def rows_to_markdown(
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    max_rows: int = 50,
) -> str:
    """把查询结果转换为 Markdown 表格，供结果汇总 prompt 使用。"""
    if not columns:
        return "（无返回列）"

    header = "| " + " | ".join(_md_cell(c) for c in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, divider]

    for idx, row in enumerate(rows):
        if idx >= max_rows:
            break
        lines.append("| " + " | ".join(_md_cell(v) for v in row) + " |")

    return "\n".join(lines)


class DBParser:
    """SQLite 数据库解析器（默认只读打开）。"""

    def __init__(self, db_path: str | Path, read_only: bool = True) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError(f"数据库文件不存在：{self.db_path}")

        self.read_only = read_only
        if read_only:
            uri = self.db_path.as_uri() + "?mode=ro"
        else:
            uri = self.db_path.as_uri()

        self.conn = sqlite3.connect(uri, uri=True, timeout=10)
        self.conn.row_factory = sqlite3.Row
        # 双保险：即使连接可写，也只允许读操作
        self.conn.execute("PRAGMA query_only = ON")

        self.table_names: list[str] = []
        self._table_meta: dict[str, dict[str, Any]] = {}
        self._table_stats: dict[str, list[dict[str, Any]]] = {}
        self._table_sample: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {}

        self._load_metadata()

    # ------------------------------------------------------------------ #
    # 元信息解析
    # ------------------------------------------------------------------ #
    def _load_metadata(self) -> None:
        rows = self.conn.execute(
            """
            SELECT name, type
            FROM sqlite_master
            WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

        for row in rows:
            table = str(row["name"])
            self.table_names.append(table)

            columns = []
            for col in self.conn.execute(
                f"PRAGMA table_info({quote_identifier(table)})"
            ).fetchall():
                columns.append(
                    {
                        "name": str(col["name"]),
                        "type": str(col["type"] or "TEXT"),
                        "nullable": not bool(col["notnull"]),
                        "default": col["dflt_value"],
                        "primary_key": bool(col["pk"]),
                    }
                )

            foreign_keys: list[dict[str, Any]] = []
            fk_rows = self.conn.execute(
                f"PRAGMA foreign_key_list({quote_identifier(table)})"
            ).fetchall()
            grouped: dict[int, list[sqlite3.Row]] = {}
            for fk in fk_rows:
                grouped.setdefault(int(fk["id"]), []).append(fk)

            for items in grouped.values():
                items.sort(key=lambda x: int(x["seq"]))
                foreign_keys.append(
                    {
                        "constrained_table": table,
                        "constrained_columns": [str(x["from"]) for x in items],
                        "referred_table": str(items[0]["table"]),
                        "referred_columns": [str(x["to"]) for x in items],
                    }
                )

            try:
                row_count = int(
                    self.conn.execute(
                        f"SELECT COUNT(*) FROM {quote_identifier(table)}"
                    ).fetchone()[0]
                )
            except sqlite3.Error:
                row_count = -1

            self._table_meta[table] = {
                "name": table,
                "type": str(row["type"]),
                "columns": columns,
                "foreign_keys": foreign_keys,
                "row_count": row_count,
            }

    def _ensure_table(self, table_name: str) -> dict[str, Any]:
        if table_name not in self._table_meta:
            raise DBParserError(
                f"表不存在：{table_name}。可用的表：{', '.join(self.table_names)}"
            )
        return self._table_meta[table_name]

    # ------------------------------------------------------------------ #
    # 字段统计（懒加载 + 缓存）
    # ------------------------------------------------------------------ #
    def _compute_stats(self, table_name: str) -> list[dict[str, Any]]:
        if table_name in self._table_stats:
            return self._table_stats[table_name]

        meta = self._ensure_table(table_name)
        stats: list[dict[str, Any]] = []

        for col in meta["columns"]:
            name = col["name"]
            qcol = quote_identifier(name)
            qtable = quote_identifier(table_name)

            try:
                agg = self.conn.execute(
                    f"""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(DISTINCT {qcol}) AS distinct_count,
                        SUM(CASE WHEN {qcol} IS NULL THEN 1 ELSE 0 END) AS null_count,
                        MIN({qcol}) AS min_value,
                        MAX({qcol}) AS max_value
                    FROM {qtable}
                    """
                ).fetchone()
            except sqlite3.Error as exc:
                raise DBParserError(f"统计字段 {table_name}.{name} 失败：{exc}") from exc

            top_values: list[tuple[Any, int]] = []
            try:
                for row in self.conn.execute(
                    f"""
                    SELECT {qcol} AS value, COUNT(*) AS cnt
                    FROM {qtable}
                    WHERE {qcol} IS NOT NULL
                    GROUP BY {qcol}
                    ORDER BY cnt DESC, value ASC
                    LIMIT 3
                    """
                ):
                    top_values.append((row["value"], int(row["cnt"])))
            except sqlite3.Error:
                top_values = []

            stats.append(
                {
                    **col,
                    "total": int(agg["total"]),
                    "distinct": int(agg["distinct_count"] or 0),
                    "null_count": int(agg["null_count"] or 0),
                    "min": agg["min_value"],
                    "max": agg["max_value"],
                    "top_values": top_values,
                }
            )

        self._table_stats[table_name] = stats
        return stats

    def parse_all(self) -> "DBParser":
        """预计算所有表的字段统计（便于演示“数据库解析”步骤）。"""
        for table_name in self.table_names:
            self._compute_stats(table_name)
        return self

    # ------------------------------------------------------------------ #
    # 对外查询接口
    # ------------------------------------------------------------------ #
    def get_table_fields(self, table_name: str) -> list[dict[str, Any]]:
        """返回字段元信息 + 统计信息。"""
        self._ensure_table(table_name)
        return self._compute_stats(table_name)

    def get_data_relations(self) -> list[dict[str, Any]]:
        """返回所有外键关系。"""
        relations: list[dict[str, Any]] = []
        for table_name in self.table_names:
            relations.extend(self._table_meta[table_name]["foreign_keys"])
        return relations

    def get_table_sample(
        self, table_name: str, limit: int = 3
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        """返回表样例数据（前 N 行）。"""
        self._ensure_table(table_name)
        cached = self._table_sample.get(table_name)
        if cached is not None and len(cached[1]) >= limit:
            return cached[0], cached[1][:limit]

        cursor = self.conn.execute(
            f"SELECT * FROM {quote_identifier(table_name)} LIMIT {int(limit)}"
        )
        sample = (
            [d[0] for d in cursor.description or []],
            [tuple(r) for r in cursor.fetchall()],
        )
        self._table_sample[table_name] = sample
        return sample

    # ------------------------------------------------------------------ #
    # 生成给大模型的 schema 文本
    # ------------------------------------------------------------------ #
    def table_overview_markdown(self) -> str:
        """紧凑的表清单，用于“表选择”步骤，控制 token。"""
        lines: list[str] = []
        for table_name in self.table_names:
            meta = self._table_meta[table_name]
            cols = []
            for col in meta["columns"]:
                mark = ""
                if col["primary_key"]:
                    mark = " PK"
                elif any(
                    col["name"] in fk["constrained_columns"]
                    for fk in meta["foreign_keys"]
                ):
                    mark = " FK"
                cols.append(f"{col['name']} {col['type']}{mark}")
            lines.append(
                f"- {table_name} ({meta['row_count']} 行): " + ", ".join(cols)
            )
        return "\n".join(lines)

    def relations_markdown(self) -> str:
        """外键关系描述，帮助模型写出正确的 JOIN。"""
        relations = self.get_data_relations()
        if not relations:
            return "（无外键关系）"
        return "\n".join(
            f"- {rel['constrained_table']}.{', '.join(rel['constrained_columns'])}"
            f" -> {rel['referred_table']}.{', '.join(rel['referred_columns'])}"
            for rel in relations
        )

    def table_schema_markdown(self, table_name: str) -> str:
        """单表详细 schema（含统计），用于 SQL 生成 prompt。"""
        meta = self._ensure_table(table_name)
        stats = self._compute_stats(table_name)

        lines = [f"### {table_name}（{meta['row_count']} 行）"]
        lines.append(
            "| 字段 | 类型 | 主键 | 非空 | 唯一值数 | 空值数 | 最小值 | 最大值 | 常见值 |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")

        for col in stats:
            top = ", ".join(
                f"{_md_cell(v, 30)}({cnt})" for v, cnt in col["top_values"][:3]
            )
            lines.append(
                "| {name} | {type} | {pk} | {notnull} | {distinct} | {nulls} | "
                "{minv} | {maxv} | {top} |".format(
                    name=_md_cell(col["name"]),
                    type=_md_cell(col["type"], 30),
                    pk="是" if col["primary_key"] else "",
                    notnull="否" if col["nullable"] else "是",
                    distinct=col["distinct"],
                    nulls=col["null_count"],
                    minv=_md_cell(col["min"], 30),
                    maxv=_md_cell(col["max"], 30),
                    top=top or "",
                )
            )

        if meta["foreign_keys"]:
            lines.append("外键：")
            for fk in meta["foreign_keys"]:
                lines.append(
                    f"- {table_name}.{', '.join(fk['constrained_columns'])}"
                    f" -> {fk['referred_table']}.{', '.join(fk['referred_columns'])}"
                )

        sample_cols, sample_rows = self.get_table_sample(table_name, limit=2)
        if sample_rows:
            lines.append("样例行：")
            lines.append(rows_to_markdown(sample_cols, sample_rows, max_rows=2))

        return "\n".join(lines)

    def schema_markdown(self, table_names: Optional[Sequence[str]] = None) -> str:
        """多表 schema。"""
        names = list(table_names) if table_names else self.table_names
        return "\n\n".join(self.table_schema_markdown(name) for name in names)

    # ------------------------------------------------------------------ #
    # SQL 校验与执行
    # ------------------------------------------------------------------ #
    def validate_sql(self, sql: str) -> tuple[bool, str]:
        """静态校验：只读、单条语句、语法/表名/列名可解析。

        使用 ``EXPLAIN QUERY PLAN`` 编译 SQL，不会真正执行。
        返回 ``(是否通过, 错误信息)``。
        """
        normalized = normalize_sql(sql)
        if not normalized:
            return False, "SQL 为空"

        masked = mask_literals_and_comments(normalized)

        # 允许一个结尾分号（后面可以跟注释），但不允许多条语句
        semicolons = [match.start() for match in re.finditer(";", masked)]
        if len(semicolons) > 1 or (
            semicolons and masked[semicolons[-1] + 1 :].strip()
        ):
            return False, "只允许执行一条 SQL 语句（检测到多余的分号）"

        if not re.match(r"^\s*(SELECT|WITH)\b", masked, re.IGNORECASE):
            return False, "只允许只读查询：SQL 必须以 SELECT 或 WITH 开头"

        for keyword in FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{keyword}\b", masked, re.IGNORECASE):
                return False, f"检测到非只读关键字：{keyword}"

        try:
            self.conn.execute("EXPLAIN QUERY PLAN " + normalized).fetchall()
        except sqlite3.Error as exc:
            return False, f"SQL 编译失败：{exc}"

        return True, "ok"

    def check_sql(self, sql: str) -> tuple[bool, str]:
        """兼容 notebook 中的命名。"""
        return self.validate_sql(sql)

    def execute_sql(
        self,
        sql: str,
        max_rows: int = 100,
        timeout: float = 30.0,
    ) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        """执行只读 SQL。

        返回 ``(列名, 行数据, 是否被截断)``。会先做只读校验；
        通过 progress handler 防止超长查询拖死进程。
        """
        ok, message = self.validate_sql(sql)
        if not ok:
            raise DBParserError(message)

        normalized = normalize_sql(sql)
        deadline = time.monotonic() + max(1.0, timeout)

        def _progress_handler() -> int:
            return 1 if time.monotonic() > deadline else 0

        self.conn.set_progress_handler(_progress_handler, 10_000)
        try:
            cursor = self.conn.execute(normalized)
            columns = [d[0] for d in cursor.description or []]
            rows = cursor.fetchmany(int(max_rows) + 1)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise DBParserError(
                    f"SQL 执行超时（>{timeout:.0f}s），请缩小查询范围"
                ) from exc
            raise DBParserError(f"SQL 执行失败：{exc}") from exc
        except sqlite3.Error as exc:
            raise DBParserError(f"SQL 执行失败：{exc}") from exc
        finally:
            self.conn.set_progress_handler(None, 0)

        truncated = len(rows) > max_rows
        return columns, [tuple(r) for r in rows[:max_rows]], truncated

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "DBParser":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
