"""图节点工厂集合。每个工厂返回 (AgentState) -> dict 的节点函数，
依赖（catalog / validator / executor / llm）通过闭包注入，不进图状态。
"""

from .execution import make_execute_sql_node, make_validate_sql_node
from .finalize import make_finalize_node
from .generation import make_generate_sql_node
from .repair import make_rewrite_sql_node
from .selection import make_select_tables_node
from .summary import make_summarize_node

__all__ = [
    "make_execute_sql_node",
    "make_finalize_node",
    "make_generate_sql_node",
    "make_rewrite_sql_node",
    "make_select_tables_node",
    "make_summarize_node",
    "make_validate_sql_node",
]
