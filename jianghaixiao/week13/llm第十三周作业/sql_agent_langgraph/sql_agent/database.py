from __future__ import annotations

from datetime import date, datetime, time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
import json
import re
import time as time_module

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine


class UnsafeQueryError(ValueError):
    """Raised when generated SQL violates the read-only policy."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    elapsed_ms: int


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


class DatabaseCatalog:
    """Read-only SQLite/relational database catalog and query executor."""

    _FORBIDDEN_SQL = re.compile(
        r"\b("
        r"insert|update|delete|drop|alter|create|replace|truncate|"
        r"attach|detach|pragma|vacuum|reindex|grant|revoke"
        r")\b",
        re.IGNORECASE,
    )

    def __init__(self, database_url: str, row_limit: int = 100) -> None:
        self.database_url = database_url
        self.row_limit = row_limit
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self.inspector = inspect(self.engine)
        self.table_names = [
            name
            for name in self.inspector.get_table_names()
            if not name.startswith("sqlite_")
        ]
        if not self.table_names:
            raise RuntimeError(f"数据库中没有可用表: {database_url}")

        self._table_schema: dict[str, str] = {
            table_name: self._describe_table(table_name)
            for table_name in self.table_names
        }

    def _describe_table(self, table_name: str) -> str:
        columns = self.inspector.get_columns(table_name)
        primary_keys = set(
            (self.inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
        )
        foreign_keys = self.inspector.get_foreign_keys(table_name)

        lines = [f"TABLE {table_name}", "COLUMNS:"]
        for column in columns:
            flags: list[str] = []
            if column.get("primary_key") or column["name"] in primary_keys:
                flags.append("PRIMARY KEY")
            if not column.get("nullable", True):
                flags.append("NOT NULL")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            lines.append(f"  - {column['name']} {column.get('type', '')}{suffix}")

        if foreign_keys:
            lines.append("FOREIGN KEYS:")
            for fk in foreign_keys:
                local = ", ".join(fk.get("constrained_columns") or [])
                referred_table = fk.get("referred_table", "")
                referred = ", ".join(fk.get("referred_columns") or [])
                lines.append(f"  - {local} -> {referred_table}.{referred}")

        samples = self._sample_rows(table_name)
        if samples:
            lines.append("SAMPLE ROWS:")
            for sample in samples:
                lines.append(
                    "  "
                    + json.dumps(sample, ensure_ascii=False, default=str)
                )

        return "\n".join(lines)

    def _sample_rows(self, table_name: str, limit: int = 2) -> list[dict[str, Any]]:
        query = (
            f"SELECT * FROM {_quote_identifier(table_name)} "
            f"LIMIT {max(1, int(limit))}"
        )
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(query)
            columns = list(result.keys())
            return [
                {
                    column: _json_safe(value)
                    for column, value in zip(columns, row)
                }
                for row in result.fetchall()
            ]

    def get_schema(self, table_names: list[str] | None = None) -> str:
        selected = table_names or self.table_names
        valid = [
            table
            for table in selected
            if table in self._table_schema
        ]
        return "\n\n".join(self._table_schema[table] for table in valid)

    def execute(self, sql: str) -> QueryResult:
        statement = sql.strip()
        while statement.endswith(";"):
            statement = statement[:-1].rstrip()

        if not statement:
            raise UnsafeQueryError("SQL 为空")
        if ";" in statement:
            raise UnsafeQueryError("只允许执行单条 SQL")
        if not re.match(r"^(select|with)\b", statement, re.IGNORECASE):
            raise UnsafeQueryError("只允许 SELECT 或 WITH 查询")
        if self._FORBIDDEN_SQL.search(statement):
            raise UnsafeQueryError("SQL 包含禁止的写操作或管理命令")

        started = time_module.perf_counter()
        try:
            with self.engine.connect() as connection:
                if self.database_url.startswith("sqlite"):
                    connection.exec_driver_sql("PRAGMA query_only = ON")

                result = connection.exec_driver_sql(statement)
                columns = list(result.keys())
                fetched = result.fetchmany(self.row_limit)
                rows = [
                    {
                        column: _json_safe(value)
                        for column, value in zip(columns, row)
                    }
                    for row in fetched
                ]
        finally:
            elapsed_ms = int((time_module.perf_counter() - started) * 1000)

        return QueryResult(
            columns=columns,
            rows=rows,
            elapsed_ms=elapsed_ms,
        )

