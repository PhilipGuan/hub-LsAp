"""LangChain 工具：把 SQL Agent 的能力暴露给模型（骨架）。"""
import json

from langchain.tools import tool

from config import MAX_ROWS
from db_parser import DBParser
from executor import execute_sql
from sql_validator import dry_run, static_check


def build_tools(parser: DBParser):
    """构建绑定当前数据库的工具列表。"""

    @tool
    def list_tables() -> str:
        """列出数据库中所有表名和中文别名。

        TODO:
        1. 遍历 parser.table_names
        2. 返回 json.dumps({"tables": [...]}, ensure_ascii=False)
        """
        raise NotImplementedError("TODO: 实现 list_tables")

    @tool
    def get_table_schema(table_names: list[str]) -> str:
        """获取指定表的字段、类型、主键、外键和样例行。

        TODO:
        1. 校验 table_names 都在 parser.table_names 里
        2. return parser.get_schema_text(table_names)
        """
        raise NotImplementedError("TODO: 实现 get_table_schema")

    @tool
    def run_sql(sql: str) -> str:
        """校验并只读执行一条 SELECT，返回 JSON 结果。

        TODO:
        1. ok, err = static_check(sql, parser)
        2. ok, err = dry_run(sql, parser.engine)
        3. result = execute_sql(sql, parser.engine, MAX_ROWS)
        4. return json.dumps({"ok": True, **result}, ensure_ascii=False, default=str)
        """
        raise NotImplementedError("TODO: 实现 run_sql")

    return [list_tables, get_table_schema, run_sql]