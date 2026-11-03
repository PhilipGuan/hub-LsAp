"""LangGraph 图状态定义。

状态里只有数据（可序列化），catalog / validator / executor / llm 等
运行时依赖由 build_graph() 闭包注入，不进入状态。
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from .models import (
    ExecutionResult,
    FinalAnswer,
    SQLAttempt,
    SQLDraft,
    TableSelection,
    ValidationResult,
    WorkflowOutput,
)


class AgentState(TypedDict):
    """一次查询在图中的完整共享状态。

    - 只有 attempts 使用 reducer（operator.add）：改写 Agent 需要看到
      "已尝试过的 SQL" 以避免重复，必须追加而非覆盖（教程 01 的写法）。
    - 其余字段均为 last-value 覆盖语义。
    """

    # 输入
    question: str
    # 选表阶段
    selection: TableSelection | None
    selection_feedback: str  # 路由反馈给选表 Agent 的纠错信息
    selection_retries: int  # 选表重试计数（上限 max_selection_retries）
    schema_context: str  # 已选表的详细 schema（generate_sql 节点装配）
    # SQL 生成-校验-执行-修复循环
    sql: str
    draft: SQLDraft | None
    attempts: Annotated[list[SQLAttempt], operator.add]
    validation: ValidationResult | None  # 校验结果；执行失败时由 execute_sql 覆写为错误
    execution: ExecutionResult | None
    repair_attempts: int  # 修复预算计数（上限 max_repair_attempts，校验/执行共享）
    # 汇总与输出
    answer: FinalAnswer | None
    output: WorkflowOutput | None  # finalize 节点组装，即为终态
