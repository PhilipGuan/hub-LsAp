"""共享工具层测试：`@tool` 与 `run_readonly_sql()` 是两条路径的唯一 SQL 执行入口。"""

from __future__ import annotations

from sqlagent.tools import build_tools, run_readonly_sql, schema_context, table_catalog


def test_table_catalog_lists_all_tables(parser):
    text = table_catalog(parser)
    assert "tracks" in text and "albums" in text


def test_schema_context_contains_fields(parser):
    text = schema_context(["tracks"], parser)
    assert "### 表 tracks" in text
    assert "TrackId" in text


def test_run_readonly_sql_executes_select(parser):
    out = run_readonly_sql(parser, "SELECT COUNT(*) FROM tracks;")
    assert "3503" in out and out.startswith("columns=")


def test_run_readonly_sql_refuses_write(parser):
    out = run_readonly_sql(parser, "DROP TABLE tracks;")
    assert out.startswith("拒绝执行")


def test_run_readonly_sql_reports_bad_sql(parser):
    out = run_readonly_sql(parser, "SELECT nope FROM tracks;")
    assert out.startswith("SQL 执行失败")


def test_build_tools_exposes_three_named_tools(parser):
    tools = build_tools(parser)
    names = {t.name for t in tools}
    assert names == {"list_tables", "parse_table", "run_sql"}
    # LangChain 会从 docstring 生成描述，模型靠它决定何时调用
    assert all(t.description for t in tools)


def test_parse_table_handles_unknown_table(parser):
    parse_table = {t.name: t for t in build_tools(parser)}["parse_table"]
    out = parse_table.invoke({"table_name": "no_such_table"})
    assert "未找到表" in out
