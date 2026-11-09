from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .privacy import is_sensitive_name, mask_value

CATALOG_VERSION = 2
MAX_BRIDGE_HOPS = 4


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _is_text(type_name: str) -> bool:
    t = (type_name or "").lower()
    return any(k in t for k in ("char", "text", "clob")) or not t


def _is_date(type_name: str) -> bool:
    t = (type_name or "").lower()
    return any(k in t for k in ("date", "time"))


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    primary_key: bool
    default: Any = None
    distinct_count: int = 0
    null_count: int = 0
    min_value: Any = None
    max_value: Any = None
    top_values: list[Any] = field(default_factory=list)
    sensitive: bool = False


@dataclass
class ForeignKeyInfo:
    table: str
    columns: list[str]
    referred_table: str
    referred_columns: list[str]


@dataclass
class TableInfo:
    name: str
    row_count: int
    columns: list[ColumnInfo]
    sample_rows: list[dict[str, Any]]
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)
    sensitive: bool = False


@dataclass
class SelectionResult:
    tables: list[str]
    reason: str = ""
    used_fallback: bool = False
    requested_tables: list[str] = field(default_factory=list)
    bridge_tables: list[str] = field(default_factory=list)
    confidence: str = "high"
    warnings: list[dict[str, str]] = field(default_factory=list)
    fallback_all: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": list(self.tables),
            "reason": self.reason,
            "used_fallback": self.used_fallback,
            "requested_tables": list(self.requested_tables),
            "bridge_tables": list(self.bridge_tables),
            "confidence": self.confidence,
            "warnings": list(self.warnings),
            "fallback_all": self.fallback_all,
        }


class DBCatalog:
    """解析 SQLite 数据库结构、样例与取值分布，并缓存为 JSON。"""

    def __init__(self, db_path: str | Path, cache_dir: str | Path | None = None):
        self.db_path = Path(db_path).resolve()
        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库不存在: {self.db_path}")
        self.cache_dir = Path(cache_dir) if cache_dir else self.db_path.parent / ".cache"
        self.tables: dict[str, TableInfo] = {}
        self.foreign_keys: list[ForeignKeyInfo] = []
        self._lower_index: dict[str, str] = {}
        self.data_from: str | None = None
        self.data_until: str | None = None
        self.fingerprint: str = ""

    # ---------- loading ----------

    def load(self, force: bool = False) -> "DBCatalog":
        cache_file = self.cache_dir / "catalog.json"
        db_mtime = self.db_path.stat().st_mtime
        if not force and cache_file.exists() and cache_file.stat().st_mtime >= db_mtime:
            try:
                self._load_cache(cache_file)
                return self
            except (KeyError, TypeError, json.JSONDecodeError):
                pass
        self._parse()
        self._save_cache(cache_file)
        return self

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)

    def _schema_signature(self, conn: sqlite3.Connection) -> str:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table','index','view','trigger') ORDER BY type, name"
        ).fetchall()
        return "\n".join(f"{row[0]}|{row[1]}|{row[2] or ''}" for row in rows)

    def _compute_fingerprint(self, schema_signature: str) -> str:
        stat = self.db_path.stat()
        raw = f"{self.db_path}|{stat.st_size}|{stat.st_mtime_ns}|{schema_signature}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _parse(self) -> None:
        conn = self._connect()
        try:
            schema_signature = self._schema_signature(conn)
            self.fingerprint = self._compute_fingerprint(schema_signature)
            names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for name in names:
                self.tables[name] = self._profile_table(conn, name)
                self.foreign_keys.extend(self.tables[name].foreign_keys)
            self._lower_index = {name.lower(): name for name in self.tables}
            self._compute_data_bounds(conn)
        finally:
            conn.close()

    def _compute_data_bounds(self, conn: sqlite3.Connection) -> None:
        self.data_from = None
        self.data_until = None
        if "invoices" in self.tables and self.has_column("invoices", "InvoiceDate"):
            try:
                row = conn.execute(
                    "SELECT MIN(InvoiceDate), MAX(InvoiceDate) FROM invoices "
                    "WHERE InvoiceDate IS NOT NULL"
                ).fetchone()
                if row and row[1]:
                    self.data_from = str(row[0])[:10]
                    self.data_until = str(row[1])[:10]
                    return
            except sqlite3.Error:
                pass
        dates: list[tuple[str, str]] = []
        for table in self.tables.values():
            for column in table.columns:
                if _is_date(column.type):
                    dates.append((table.name, column.name))
        for table, column in dates:
            try:
                row = conn.execute(
                    f"SELECT MIN({_quote(column)}), MAX({_quote(column)}) "
                    f"FROM {_quote(table)}"
                ).fetchone()
            except sqlite3.Error:
                continue
            if row and row[0] and row[1]:
                low, high = str(row[0])[:10], str(row[1])[:10]
                self.data_from = min(self.data_from or low, low)
                self.data_until = max(self.data_until or high, high)

    def _profile_table(self, conn: sqlite3.Connection, table: str) -> TableInfo:
        tq = _quote(table)
        row_count = conn.execute(f"SELECT COUNT(*) FROM {tq}").fetchone()[0]

        fk_by_id: dict[int, dict[str, Any]] = {}
        for row in conn.execute(f"PRAGMA foreign_key_list({_sql_str(table)})"):
            fk_id, _seq, ref_table, from_col, to_col = row[0], row[1], row[2], row[3], row[4]
            entry = fk_by_id.setdefault(
                fk_id,
                {
                    "table": table,
                    "columns": [],
                    "referred_table": ref_table,
                    "referred_columns": [],
                },
            )
            entry["columns"].append(from_col)
            entry["referred_columns"].append(to_col)
        foreign_keys = [ForeignKeyInfo(**entry) for entry in fk_by_id.values()]

        columns: list[ColumnInfo] = []
        for _cid, name, type_name, notnull, default, pk in conn.execute(
            f"PRAGMA table_info({_sql_str(table)})"
        ):
            sensitive = is_sensitive_name(name)
            columns.append(
                ColumnInfo(
                    name=name,
                    type=type_name or "",
                    nullable=not bool(notnull),
                    primary_key=bool(pk),
                    default=_jsonable(default),
                    sensitive=sensitive,
                    **_profile_column(conn, table, name, type_name or "", sensitive),
                )
            )

        sensitive_names = {column.name for column in columns if column.sensitive}
        cursor = conn.execute(f"SELECT * FROM {tq} LIMIT 3")
        col_names = [d[0] for d in cursor.description or []]
        sample_rows = [
            {
                col: "***" if col in sensitive_names else _jsonable(value)
                for col, value in zip(col_names, row)
            }
            for row in cursor.fetchall()
        ]

        return TableInfo(
            name=table,
            row_count=row_count,
            columns=columns,
            sample_rows=sample_rows,
            foreign_keys=foreign_keys,
            sensitive=bool(sensitive_names),
        )

    # ---------- cache ----------

    def _save_cache(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CATALOG_VERSION,
            "db_path": str(self.db_path),
            "fingerprint": self.fingerprint,
            "data_from": self.data_from,
            "data_until": self.data_until,
            "tables": [asdict(t) for t in self.tables.values()],
            "foreign_keys": [asdict(fk) for fk in self.foreign_keys],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_cache(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != CATALOG_VERSION:
            raise KeyError("catalog cache version mismatch")
        self.fingerprint = payload.get("fingerprint") or ""
        self.data_from = payload.get("data_from")
        self.data_until = payload.get("data_until")
        self.tables = {}
        for t in payload["tables"]:
            t["columns"] = [ColumnInfo(**c) for c in t["columns"]]
            t["foreign_keys"] = [ForeignKeyInfo(**fk) for fk in t["foreign_keys"]]
            table = TableInfo(**t)
            self.tables[table.name] = table
        self.foreign_keys = [ForeignKeyInfo(**fk) for fk in payload["foreign_keys"]]
        self._lower_index = {name.lower(): name for name in self.tables}

    # ---------- metadata helpers ----------

    @property
    def table_names(self) -> list[str]:
        return list(self.tables)

    def has_table(self, name: str) -> bool:
        return name.lower() in self._lower_index

    def resolve_table(self, name: str) -> str | None:
        return self._lower_index.get(name.lower())

    def column_names(self, table: str) -> set[str]:
        real = self.resolve_table(table)
        if not real:
            return set()
        return {c.name.lower() for c in self.tables[real].columns}

    def has_column(self, table: str, column: str) -> bool:
        return column.lower() in self.column_names(table)

    def get_table(self, name: str) -> TableInfo:
        real = self.resolve_table(name)
        if not real:
            raise KeyError(f"未知表: {name}")
        return self.tables[real]

    def column_info(self, table: str, column: str) -> ColumnInfo | None:
        real = self.resolve_table(table)
        if not real:
            return None
        for info in self.tables[real].columns:
            if info.name.lower() == column.lower():
                return info
        return None

    def is_sensitive_column(self, table: str, column: str) -> bool:
        info = self.column_info(table, column)
        if info is not None:
            return info.sensitive
        return is_sensitive_name(column)

    def sensitive_columns(self, table: str) -> set[str]:
        real = self.resolve_table(table)
        if not real:
            return set()
        return {c.name.lower() for c in self.tables[real].columns if c.sensitive}

    def base_row_count(self, tables: list[str]) -> int | None:
        counts = [
            self.tables[name].row_count
            for name in tables
            if name in self.tables
        ]
        return max(counts) if counts else None

    # ---------- prompt rendering ----------

    def table_ddl(self, table: str) -> str:
        info = self.get_table(table)
        fk_by_column = {
            fk.columns[0]: fk for fk in info.foreign_keys if len(fk.columns) == 1
        }
        lines: list[str] = []
        last = len(info.columns) - 1
        for idx, col in enumerate(info.columns):
            parts = [_quote(col.name)]
            if col.type:
                parts.append(col.type)
            if col.primary_key:
                parts.append("PRIMARY KEY")
            line = " ".join(parts) + (", " if idx < last else "")
            fk = fk_by_column.get(col.name)
            if fk:
                line += f" -- FK -> {fk.referred_table}.{fk.referred_columns[0]}"
            if col.sensitive:
                line += " -- sensitive: do not project"
            lines.append("  " + line.rstrip())
        body = ",\n".join(lines)
        return (
            f"-- {info.name} ({info.row_count} 行)\n"
            f"CREATE TABLE {_quote(info.name)} (\n{body}\n);"
        )

    def ddl(self, tables: list[str] | None = None) -> str:
        names = tables or self.table_names
        return "\n\n".join(self.table_ddl(name) for name in names)

    def compact_catalog(self) -> str:
        lines: list[str] = []
        for info in self.tables.values():
            cols = ", ".join(c.name for c in info.columns)
            lines.append(f"- {info.name} ({info.row_count} 行): {cols}")
            hints = []
            for col in info.columns:
                if _useful_values(col) and col.top_values:
                    values = ", ".join(str(v) for v in col.top_values[:12])
                    hints.append(f"{col.name}=[{values}]")
            if hints:
                lines.append("  值域: " + "; ".join(hints))
        fk_text = "; ".join(
            f"{fk.table}.{','.join(fk.columns)} -> "
            f"{fk.referred_table}.{','.join(fk.referred_columns)}"
            for fk in self.foreign_keys
        )
        data_bounds = f"\n数据范围: {self.data_from or '?'} ~ {self.data_until or '?'}"
        return "\n".join(lines) + f"\n外键关系: {fk_text}" + data_bounds

    def schema_text(self, tables: list[str] | None = None) -> str:
        names = tables or self.table_names
        blocks: list[str] = [
            f"-- 数据范围: {self.data_from or '?'} ~ {self.data_until or '?'}"
        ]
        for name in names:
            info = self.get_table(name)
            blocks.append(self.table_ddl(info.name))
            blocks.append(_samples_markdown(info))
            hints = _value_hints(info)
            if hints:
                blocks.append("值域提示:\n" + hints)
        return "\n\n".join(blocks)

    # ---------- foreign key graph ----------

    def _adjacency(self) -> dict[str, set[str]]:
        graph: dict[str, set[str]] = {name: set() for name in self.tables}
        for fk in self.foreign_keys:
            a, b = fk.table, fk.referred_table
            if a in graph and b in graph:
                graph[a].add(b)
                graph[b].add(a)
        return graph

    def connect_tables(
        self, selected: list[str], max_hops: int = MAX_BRIDGE_HOPS
    ) -> list[str]:
        """沿外键图补全连接多张表所需的中间表。"""
        resolved = [self.resolve_table(t) for t in selected]
        chosen = [t for t in resolved if t]
        if len(chosen) <= 1:
            return chosen

        graph = self._adjacency()
        needed: set[str] = set(chosen)
        for i, start in enumerate(chosen):
            for end in chosen[i + 1 :]:
                path = _shortest_path(graph, start, end)
                if path and len(path) - 1 <= max_hops:
                    needed.update(path)

        order = {name: idx for idx, name in enumerate(self.tables)}
        return sorted(needed, key=lambda t: order.get(t, 999))


def _profile_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    type_name: str,
    sensitive: bool,
) -> dict[str, Any]:
    tq, cq = _quote(table), _quote(column)
    try:
        nulls = conn.execute(
            f"SELECT COUNT(*) FROM {tq} WHERE {cq} IS NULL"
        ).fetchone()[0]
    except sqlite3.Error:
        return {}
    if sensitive:
        return {
            "distinct_count": 0,
            "null_count": nulls,
            "min_value": None,
            "max_value": None,
            "top_values": [],
        }
    try:
        distinct = conn.execute(
            f"SELECT COUNT(DISTINCT {cq}) FROM {tq}"
        ).fetchone()[0]
        min_v = conn.execute(f"SELECT MIN({cq}) FROM {tq}").fetchone()[0]
        max_v = conn.execute(f"SELECT MAX({cq}) FROM {tq}").fetchone()[0]
    except sqlite3.Error:
        return {}

    top_values: list[Any] = []
    if _is_text(type_name) and not _is_date(type_name) and distinct <= 50:
        try:
            rows = conn.execute(
                f"SELECT {cq}, COUNT(*) AS c FROM {tq} "
                f"WHERE {cq} IS NOT NULL GROUP BY {cq} "
                f"ORDER BY c DESC, {cq} LIMIT 20"
            ).fetchall()
            top_values = [_jsonable(row[0]) for row in rows]
        except sqlite3.Error:
            top_values = []

    return {
        "distinct_count": distinct,
        "null_count": nulls,
        "min_value": _jsonable(min_v),
        "max_value": _jsonable(max_v),
        "top_values": top_values,
    }


def _useful_values(column: ColumnInfo) -> bool:
    return (
        not column.sensitive
        and _is_text(column.type)
        and not _is_date(column.type)
        and 1 < column.distinct_count <= 30
        and column.top_values != []
    )


def _samples_markdown(info: TableInfo) -> str:
    if not info.sample_rows:
        return "样例行：无"
    headers = [c.name for c in info.columns]
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in info.sample_rows:
        cells = [_cell(row.get(h)) for h in headers]
        lines.append("| " + " | ".join(cells) + " |")
    return "样例行:\n" + "\n".join(lines)


def _cell(value: Any) -> str:
    if value is None:
        return "NULL"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text[:60] + ("…" if len(text) > 60 else "")


def _value_hints(info: TableInfo) -> str:
    lines: list[str] = []
    for col in info.columns:
        if col.sensitive:
            continue
        if _useful_values(col):
            values = ", ".join(str(v) for v in col.top_values)
            suffix = "（按出现频率排序）" if col.distinct_count > len(col.top_values) else ""
            lines.append(f"- {col.name} ({col.distinct_count} 个不同值): {values}{suffix}")
        elif _is_date(col.type) and col.min_value is not None and col.max_value is not None:
            lines.append(f"- {col.name}: 时间范围 [{col.min_value}, {col.max_value}]")
        elif not _is_text(col.type) and col.min_value is not None and col.max_value is not None:
            lines.append(f"- {col.name}: 范围 [{col.min_value}, {col.max_value}]")
    return "\n".join(lines)


def _shortest_path(graph: dict[str, set[str]], start: str, end: str) -> list[str]:
    if start == end:
        return [start]
    queue: list[list[str]] = [[start]]
    visited = {start}
    while queue:
        path = queue.pop(0)
        node = path[-1]
        for neighbor in sorted(graph.get(node, ())):
            if neighbor in visited:
                continue
            if neighbor == end:
                return path + [neighbor]
            visited.add(neighbor)
            queue.append(path + [neighbor])
    return []
