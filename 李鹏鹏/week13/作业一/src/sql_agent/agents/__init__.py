"""四个 LLM 步骤的 LangChain 链定义（LCEL：prompt | structured model）。

每个模块暴露一个带缓存的链工厂和一个异步调用函数；链在首次调用时才构造，
因此导入包、运行无 LLM 的单元测试都不需要 API key。
"""

from __future__ import annotations

from .result_summarizer import get_result_summary_chain, summarize
from .sql_generator import generate_sql, get_sql_generation_chain
from .sql_rewriter import get_sql_rewrite_chain, rewrite_sql
from .table_selector import get_table_selection_chain, select_tables

__all__ = [
    "generate_sql",
    "get_result_summary_chain",
    "get_sql_generation_chain",
    "get_sql_rewrite_chain",
    "get_table_selection_chain",
    "rewrite_sql",
    "select_tables",
    "summarize",
]
