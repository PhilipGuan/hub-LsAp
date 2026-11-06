"""图状态单测：验证 attempts 的 operator.add reducer 语义。"""

import operator
import typing

from sql_agent_langgraph.graph_state import AgentState
from sql_agent_langgraph.models import SQLAttempt


def test_attempts_uses_operator_add_reducer() -> None:
    # from __future__ import annotations 使注解为字符串，需求值后检查。
    hints = typing.get_type_hints(AgentState, include_extras=True)
    metadata = hints["attempts"].__metadata__
    assert operator.add in metadata, (
        "attempts 必须声明 Annotated[list[SQLAttempt], operator.add]"
    )


def test_attempts_accumulate_across_nodes() -> None:
    """模拟 reducer 依次合并两个节点的追加更新。

    LangGraph 对 Annotated[list, operator.add] 的合并方式是
    operator.add(已有值, 新值)，即新节点的追加排在列表尾部。
    """

    update_generator = {"attempts": [SQLAttempt(sql="SELECT 1", source="generator")]}
    update_rewriter = {"attempts": [SQLAttempt(sql="SELECT 2", source="rewriter")]}

    merged: list[SQLAttempt] = update_generator["attempts"]
    merged = operator.add(merged, update_rewriter["attempts"])

    assert [attempt.source for attempt in merged] == ["generator", "rewriter"]
