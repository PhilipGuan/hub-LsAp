"""确定性组件测试（无 LLM）：catalog / validator / executor。

用例与原实现 tests/test_db_tools.py 对齐，验证复用层行为未变。
"""

from sql_agent_langgraph.db.catalog import SchemaCatalog
from sql_agent_langgraph.db.executor import SQLExecutionError
from sql_agent_langgraph.db.validator import normalize_sql


def test_catalog_excludes_sqlite_internal_tables(real_catalog: SchemaCatalog) -> None:
    assert len(real_catalog.table_names) == 11
    assert "tracks" in real_catalog.table_names
    assert "sqlite_sequence" not in real_catalog.table_names
    assert "sqlite_stat1" not in real_catalog.table_names


def test_catalog_has_multiple_sample_rows(real_catalog: SchemaCatalog) -> None:
    profile = real_catalog._tables["albums"]
    assert profile.row_count == 347
    assert len(profile.sample_rows) == 3


def test_catalog_renders_join_edges(real_catalog: SchemaCatalog) -> None:
    edges = [
        edge.render()
        for edge in real_catalog.join_edges(["invoice_items", "invoices"])
    ]
    assert "invoice_items.InvoiceId -> invoices.InvoiceId" in edges
    assert real_catalog.is_connected(["invoice_items", "invoices", "customers"])
    assert not real_catalog.is_connected(["albums", "customers"])


def test_validator_security_and_syntax(validator) -> None:
    ok_cases = [
        "SELECT COUNT(DISTINCT ArtistId) FROM albums;",
        "WITH x AS (SELECT 1 AS n) SELECT * FROM x",
        "SELECT 'DROP' AS word",
    ]
    for sql in ok_cases:
        result = validator.validate(sql)
        assert result.ok, f"{sql} 应通过：{result.message}"
        assert not result.normalized_sql.endswith(";")

    bad_cases = [
        "DROP TABLE tracks",
        "SELECT 1; DROP TABLE tracks",
        "SELECT nope FROM tracks",
        "",
    ]
    for sql in bad_cases:
        result = validator.validate(sql)
        assert not result.ok, f"{sql} 应被拒绝"
        assert result.code is not None


def test_validator_does_not_execute(validator) -> None:
    result = validator.validate("CREATE TABLE should_not_exist(x int)")
    assert not result.ok
    assert result.code.value == "unsafe"


def test_normalize_sql_removes_fence_and_semicolon() -> None:
    assert normalize_sql("```sql\nSELECT 1;\n```") == "SELECT 1"


def test_executor_returns_rows_and_truncates(db_path) -> None:
    from sql_agent_langgraph.db.executor import SQLExecutor

    executor = SQLExecutor(db_path, max_rows=5)
    result = executor.execute("SELECT TrackId, Name FROM tracks ORDER BY TrackId")
    assert result.columns == ["TrackId", "Name"]
    assert result.row_count == 5
    assert result.truncated is True
    assert result.rows[0][1] == "For Those About To Rock (We Salute You)"


def test_executor_is_readonly(executor) -> None:
    try:
        executor.execute("CREATE TABLE should_not_exist(x int)")
    except SQLExecutionError:
        pass  # 只读连接必须拒绝写操作
    else:
        raise AssertionError("写操作未被拒绝")
