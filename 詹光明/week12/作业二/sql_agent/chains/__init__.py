"""四个 LangChain 链的入口（对应参考实现的四个 Agent）。

每个链都是 ``ChatPromptTemplate | ChatOpenAI.with_structured_output(...)``，
由 workflow 显式调用，保持与 OpenAI Agents SDK 版本相同的编排方式。
"""

from .result_summarizer import get_result_summary_chain
from .sql_generator import get_sql_generator_chain
from .sql_rewriter import get_sql_rewriter_chain
from .table_selector import get_table_selector_chain

__all__ = [
    "get_result_summary_chain",
    "get_sql_generator_chain",
    "get_sql_rewriter_chain",
    "get_table_selector_chain",
]
