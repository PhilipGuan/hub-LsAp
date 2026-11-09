from __future__ import annotations

import pytest

from sql_agent.validator import SQLValidator


@pytest.fixture(scope="module")
def validator(catalog):
    return SQLValidator(catalog)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) AS n FROM invoices",
        "```sql\nSELECT COUNT(*) AS n FROM invoices;\n```",
        "-- 数据截止 2013-12-22\nSELECT COUNT(*) AS n FROM invoices;",
        "WITH t AS (SELECT InvoiceId, Total FROM invoices) SELECT COUNT(*) FROM t",
        "SELECT i.BillingCountry, SUM(i.Total) AS revenue "
        "FROM invoices i GROUP BY i.BillingCountry ORDER BY revenue DESC",
        "SELECT p.Name, COUNT(pt.TrackId) AS n FROM playlists p "
        "LEFT JOIN playlist_track pt ON p.PlaylistId = pt.PlaylistId "
        "GROUP BY p.PlaylistId",
    ],
)
def test_valid_queries(validator: SQLValidator, sql: str) -> None:
    result = validator.validate(sql)
    assert result.ok, f"{result.code} {result.stage} {result.error}"


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DROP TABLE invoices", "E5003"),
        ("DELETE FROM invoices", "E5003"),
        ("SELECT 1; SELECT 2", "E5002"),
        ("SELECT 1; DROP TABLE tracks", "E5002"),
        ("SELECT * FROM customers", "E5012"),
        ("SELECT Email FROM customers", "E8007"),
        ("SELECT LOWER(Email) FROM customers", "E8007"),
        ("SELECT load_extension('evil')", "E5011"),
        ("ATTACH DATABASE 'x.db' AS x", "E5003"),
        ("SELECT * FROM nope", "E5006"),
        ("SELECT nope FROM invoices", "E5007"),
    ],
)
def test_blocked_queries(
    validator: SQLValidator, sql: str, code: str
) -> None:
    result = validator.validate(sql)
    assert not result.ok
    assert result.code == code, f"{result.stage}: {result.error}"


def test_cartesian_join(validator: SQLValidator) -> None:
    result = validator.validate("SELECT COUNT(*) FROM invoices, customers")
    assert not result.ok
    assert result.code == "E5014"


def test_group_by_mismatch(validator: SQLValidator) -> None:
    result = validator.validate(
        "SELECT BillingCountry, InvoiceDate, SUM(Total) AS revenue "
        "FROM invoices GROUP BY BillingCountry"
    )
    assert not result.ok
    assert result.code == "E5013"


def test_allowed_tables_whitelist(validator: SQLValidator) -> None:
    result = validator.validate(
        "SELECT COUNT(*) FROM customers", allowed_tables=["invoices"]
    )
    assert not result.ok
    assert result.code == "E5006"


def test_sensitive_columns_allowed_in_count(validator: SQLValidator) -> None:
    result = validator.validate("SELECT COUNT(Email) AS n FROM customers")
    assert result.ok, result.error


def test_sql_too_long(validator: SQLValidator) -> None:
    sql = "SELECT '" + "x" * 30_000 + "' AS v"
    result = validator.validate(sql)
    assert not result.ok
    assert result.code == "E5016"


def test_function_allowlist(validator: SQLValidator) -> None:
    assert validator.validate("SELECT random() AS r").ok is False
    result = validator.validate("SELECT ROUND(AVG(Total), 2) AS v FROM invoices")
    assert result.ok, result.error
