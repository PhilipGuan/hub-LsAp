from sql_agent.database import DatabaseCatalog, UnsafeQueryError
from sql_agent.config import get_settings

settings = get_settings()
catalog = DatabaseCatalog(settings.database_url, row_limit=10)


def test_catalog_has_chinook_tables():
    assert "customers" in catalog.table_names
    assert "invoices" in catalog.table_names
    assert "tracks" in catalog.table_names


def test_read_only_query_returns_rows():
    result = catalog.execute("SELECT COUNT(*) AS customer_count FROM customers")
    assert result.columns == ["customer_count"]
    assert result.rows[0]["customer_count"] > 0


def test_write_query_is_rejected():
    try:
        catalog.execute("DELETE FROM customers")
    except UnsafeQueryError:
        pass
    else:
        raise AssertionError("DELETE 查询应该被拒绝")


def test_schema_contains_foreign_keys():
    schema = catalog.get_schema(["invoices", "customers"])
    assert "TABLE invoices" in schema
    assert "FOREIGN KEYS" in schema
