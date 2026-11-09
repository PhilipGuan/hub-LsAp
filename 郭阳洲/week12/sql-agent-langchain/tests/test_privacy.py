from __future__ import annotations

from sql_agent.privacy import (
    is_sensitive_name,
    mask_rows,
    mask_value,
    normalize_column,
    projection_violations,
    sanitize_log_text,
)


def test_normalize_column_prefixes() -> None:
    assert normalize_column("BillingAddress") == "address"
    assert normalize_column("shipping_city") == "city"
    assert normalize_column("Email") == "email"
    assert normalize_column("InvoiceId") == "invoiceid"


def test_sensitive_names() -> None:
    for name in (
        "Email",
        "Phone",
        "BillingAddress",
        "ShippingCity",
        "FirstName",
        "LastName",
        "PostalCode",
        "Fax",
        "Company",
    ):
        assert is_sensitive_name(name), name
    for name in ("CustomerId", "InvoiceId", "Total", "Name", "Country"):
        assert not is_sensitive_name(name), name


def test_projection_violations_star() -> None:
    violations = projection_violations("SELECT * FROM customers")
    assert any("SELECT *" in item for item in violations)


def test_projection_violations_sensitive() -> None:
    assert projection_violations("SELECT Email FROM customers")
    assert projection_violations("SELECT LOWER(Email) FROM customers")
    assert projection_violations("SELECT CustomerId FROM customers ORDER BY Email")
    assert not projection_violations("SELECT COUNT(Email) FROM customers")
    assert not projection_violations("SELECT CustomerId, Country FROM customers")


def test_mask_value_formats() -> None:
    assert mask_value("Email", "luisg@embraer.com.br") == "l***@embraer.com.br"
    assert mask_value("FirstName", "Luís") == "L***"
    assert mask_value("Phone", "+55 (12) 3923-5555") == "***5555"
    assert mask_value("PostalCode", "12227-000") == "12***"
    assert mask_value("Address", "Av. Brigadeiro Faria Lima, 2170") == "***"
    assert mask_value("Phone", None) is None


def test_mask_rows_only_touches_sensitive_columns() -> None:
    columns = ["CustomerId", "Email", "Country"]
    rows = [(1, "a@b.com", "USA")]
    masked = mask_rows(columns, rows)
    assert masked == [(1, "a***@b.com", "USA")]


def test_sanitize_log_text() -> None:
    text = sanitize_log_text("user a@b.com phone +551239235555")
    assert "a@b.com" not in text
    assert "+551239235555" not in text
