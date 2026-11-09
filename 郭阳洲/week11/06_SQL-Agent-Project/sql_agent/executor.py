from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .budget import Budget
from .privacy import is_sensitive_name, mask_rows


@dataclass
class ExecutionResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: float = 0.0
    error: str = ""
    error_code: str = ""
    masked_columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "row_count": self.row_count,
            "truncated": self.truncated,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "error": self.error,
            "error_code": self.error_code,
            "masked_columns": list(self.masked_columns),
        }


class SQLExecutor:
    """只读执行：query_only + 进度超时 + 行数上限 + 敏感列脱敏。"""

    def __init__(self, db_path: str | Path, max_rows: int = 100, timeout_s: float = 10.0):
        self.db_path = Path(db_path).resolve()
        self.max_rows = max(1, int(max_rows))
        self.timeout_s = max(0.5, float(timeout_s))

    def execute(self, sql: str, budget: Budget | None = None) -> ExecutionResult:
        start = time.perf_counter()
        if not self.db_path.exists():
            return ExecutionResult(
                ok=False,
                error=f"数据库不存在: {self.db_path}",
                error_code="E6003",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )
        if budget is not None and budget.remaining() <= 0:
            return ExecutionResult(
                ok=False,
                error="整请求预算已耗尽，未执行查询",
                error_code="E8004",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        timeout = self.timeout_s
        if budget is not None:
            timeout = min(timeout, max(0.5, budget.remaining()))
        deadline = time.monotonic() + timeout

        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                self.db_path.as_uri() + "?mode=ro", uri=True, timeout=5
            )
            conn.execute("PRAGMA query_only = ON")
            conn.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0, 10_000
            )
            cursor = conn.execute(sql)
            fetched = cursor.fetchmany(self.max_rows + 1)
            truncated = len(fetched) > self.max_rows
            rows = [tuple(row) for row in fetched[: self.max_rows]]
            columns = [desc[0] for desc in cursor.description or []]
            masked = sorted(
                {column for column in columns if is_sensitive_name(column)}
            )
            if masked:
                rows = mask_rows(columns, rows)
            return ExecutionResult(
                ok=True,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
                elapsed_ms=(time.perf_counter() - start) * 1000,
                masked_columns=masked,
            )
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if "interrupted" in message.lower():
                code, text = "E6001", f"查询超过 {timeout:.1f} 秒，已中断"
            elif "readonly" in message.lower() or "read-only" in message.lower():
                code, text = "E5010", "只允许只读查询"
            else:
                code, text = "E6002", f"SQLite 执行失败: {message}"
            return ExecutionResult(
                ok=False,
                error=text,
                error_code=code,
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )
        except sqlite3.Error as exc:
            return ExecutionResult(
                ok=False,
                error=f"SQLite 执行失败: {exc}",
                error_code="E6002",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )
        except (OSError, ValueError) as exc:
            return ExecutionResult(
                ok=False,
                error=f"数据库连接失败: {exc}",
                error_code="E6003",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )
        finally:
            if conn is not None:
                conn.close()
