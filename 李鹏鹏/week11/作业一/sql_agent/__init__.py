"""SQL Agent Workflow.

一个可拆解、可观测的 SQL Agent：

    数据库解析 -> 表选择 -> SQL 生成 -> SQL 校验 -> SQL 改写(修复) -> SQL 执行 -> 结果汇总

只依赖 Python 标准库 + openai SDK（用于调用兼容 OpenAI 协议的大模型）。
"""

from .config import Settings
from .db_parser import DBParser
from .workflow import SQLAgentWorkflow, SQLAgentResult

__all__ = ["Settings", "DBParser", "SQLAgentWorkflow", "SQLAgentResult"]
