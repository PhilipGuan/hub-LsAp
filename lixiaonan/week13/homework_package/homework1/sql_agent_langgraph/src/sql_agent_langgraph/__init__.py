"""使用 LangGraph 复现的 Chinook SQL Agent。

编排从原实现的手写状态机（openai-agents + for/if）迁移为
StateGraph + 条件边；确定性组件（schema 解析、SQL 校验、只读执行）
与 Pydantic 数据契约原样复用。
"""

from .config import settings
from .graph import SQLAgentGraph, build_graph
from .graph_state import AgentState
from .models import (
    ErrorCode,
    ExecutionResult,
    FinalAnswer,
    SQLAttempt,
    SQLDraft,
    TableSelection,
    ValidationResult,
    WorkflowOutput,
)

__version__ = "0.1.0"

__all__ = [
    "AgentState",
    "ErrorCode",
    "ExecutionResult",
    "FinalAnswer",
    "SQLAgentGraph",
    "SQLAttempt",
    "SQLDraft",
    "TableSelection",
    "ValidationResult",
    "WorkflowOutput",
    "build_graph",
    "settings",
]
