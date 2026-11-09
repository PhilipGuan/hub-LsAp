from __future__ import annotations

import pytest

from sql_agent.executor import SQLExecutor


def test_select_scalar(db_path) -> None:
    executor = SQLExecutor(db_path, max_rows=10)
    result = executor.execute("SELECT 1 AS one")
    assert result.ok
    assert result.columns == ["one"]
    assert result.rows == [(1,)]
    assert result.row_count == 1
    assert not result.truncated


def test_truncation(db_path) -> None:
    executor = SQLExecutor(db_path, max_rows=2)
    result = executor.execute("SELECT InvoiceId FROM invoices ORDER BY InvoiceId")
    assert result.ok
    assert result.truncated
    assert result.row_count == 2


def test_read_only_blocks_writes(db_path) -> None:
    executor = SQLExecutor(db_path)
    result = executor.execute("DELETE FROM invoices")
    assert not result.ok
    assert result.error_code == "E5010"


def test_missing_db(tmp_path) -> None:
    executor = SQLExecutor(tmp_path / "missing.db")
    result = executor.execute("SELECT 1")
    assert not result.ok
    assert result.error_code == "E6003"


def test_pii_masking(db_path) -> None:
    executor = SQLExecutor(db_path, max_rows=5)
    result = executor.execute("SELECT CustomerId, Email FROM customers LIMIT 1")
    assert result.ok
    assert result.masked_columns == ["Email"]
    assert "@" in str(result.rows[0][1])


def test_budget_exhausted(db_path) -> None:
    from sql_agent.budget import Budget

    now = [0.0]
    executor = SQLExecutor(db_path)
    budget = Budget(deadline_s=1.0, clock=lambda: now[0])
    now[0] = 2.0
    result = executor.execute("SELECT 1", budget=budget)
    assert not result.ok
    assert result.error_code == "E8004"
