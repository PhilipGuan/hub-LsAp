"""LangGraph 共享状态定义。

- steps / warnings / assumptions / history 为 append-only（operator.add reducer）；
- resolution / execution / empty / summary 等保存业务对象（无 checkpointer 场景下
  直接持有对象，节点间零转换成本）；
- route 由节点写入、条件边消费，取值为目标节点名。
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict


@dataclass
class TraceStep:
    name: str
    status: str
    detail: str = ""
    elapsed_ms: float = 0.0


class AgentState(TypedDict, total=False):
    # 会话标识（只读）
    question: str
    user_code: str
    trace_id: str
    started_at: str
    data_until: str

    # append-only 追踪
    steps: Annotated[list[TraceStep], operator.add]
    warnings: Annotated[list[dict[str, Any]], operator.add]
    assumptions: Annotated[list[str], operator.add]
    history: Annotated[list[dict[str, Any]], operator.add]

    # 各节点产物（业务对象）
    intent: dict[str, Any] | None
    selection: Any
    resolution: Any
    schema_text: str
    sql: str
    attempts: int
    validated: bool
    executed: bool
    execution: Any
    empty: Any
    summary: Any
    contract: Any

    # 摘要后的结果字段
    tables: list[str]
    metric: dict[str, Any] | None
    time_range: dict[str, Any] | None
    columns: list[str]
    rows: list[tuple]
    row_count: int
    truncated: bool
    masked_columns: list[str]
    empty_reason: str | None
    claims: list[dict[str, Any]]
    grounding_ok: bool
    used_fallback: bool

    # 路由与终止
    route: str
    rewrite_error: str
    status: str
    success: bool
    answer: str
    suggestions: list[str]
    error: str
    error_code: str
    error_name: str
    error_class: int
    user_message: str
