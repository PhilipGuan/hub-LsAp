"""LangGraph 回合级编排。

图结构极简：START → record → END。
实时 token 不进入图状态，只有在整轮结束后把 user_input / emotion / reply
一次性写入 checkpointer，避免 token 级 SQLite 写放大。
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph


class TurnState(TypedDict, total=False):
    user_input: str
    emotion_label: str
    confidence: float | None
    intensity: str | None
    mixed_emotions: list[dict[str, Any]] | None
    reply: str
    # 扩展字段：按回合累积的对话历史，用于下一轮拼上下文。
    history: list[dict[str, Any]]


def record(state: TurnState) -> dict[str, Any]:
    """把本回合追加进 history；其余字段由输入原样保留。"""
    history = list(state.get("history") or [])
    history.append({"role": "user", "content": state.get("user_input", "")})
    history.append(
        {
            "role": "assistant",
            "content": state.get("reply", ""),
            "emotion": state.get("emotion_label", "neutral"),
        }
    )
    return {"history": history}


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    graph = StateGraph(TurnState)
    graph.add_node("record", record)
    graph.add_edge(START, "record")
    graph.add_edge("record", END)
    return graph.compile(checkpointer=checkpointer, name="emotion-turn")
