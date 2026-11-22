"""agent 路线的多轮会话门面：`AgentBI`。

把「单 Agent + 工具 + middleware」的 `create_agent` 图，包成与 `ConversationBI`
（`sqlagent.conversation.bi`）**同形**的多轮入口 —— `ask` / `resume` / `turns` / `graph` /
`config`。这样上层的 server 只用一个 `_sessions` 字典就能同时放两种会话，前端也共用
「提问 → 若暂停则展示卡片 → 续接」这一套交互。

两条路线的差异（也是本类存在的理由）：

| | `ConversationBI`（图路线） | `AgentBI`（agent 路线） |
|---|---|---|
| 驱动 | 手写 `StateGraph`，拓扑固定 | `create_agent`，模型自主决定工具调用顺序 |
| 人工闸门 | 子图里的 `interrupt()`（`{"action": ...}`） | `HumanInTheLoopMiddleware`（`{"decisions": [...]}`） |
| 兜底 | `llm.build_chat_model` 的 `.with_fallbacks()` | `ModelFallbackMiddleware` |

`interrupt` 载荷与续接值都**保持各自框架原生的形态**（agent 侧不做归一化），
前端据此分支渲染。
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.types import Command

from .. import llm
from ..db import DBParser
from ..hitl import pending_interrupt
from ..session import make_checkpointer, new_thread_id, thread_config
from .sql_agent import _last_ai_text, build_agent_middleware, build_autonomous_agent


def _last_run_sql(messages: list) -> str | None:
    """取最近一次 `run_sql` 工具调用的 SQL（供前端展示「统计口径」）。"""
    for m in reversed(list(messages)):
        for call in (getattr(m, "tool_calls", None) or []):
            if call.get("name") == "run_sql":
                return (call.get("args") or {}).get("sql")
    return None


def _last_sql_summary(messages: list) -> dict:
    """取最近一次 `write_sql` 返回的摘要（子代理的产物，D23）。

    批 5 起 SQL 由子代理起草：父图看不见子代理的 schema 探索与自纠轨迹，只看得到这一条
    JSON —— 它是 `trace.sql` / `trace.attempts` 回填的权威来源。解析失败时返回空字典，
    回退到既有来源（`run_sql` 的调用参数），trace 形状不受影响。
    """
    for m in reversed(list(messages)):
        if getattr(m, "type", None) == "tool" and getattr(m, "name", None) == "write_sql":
            try:
                summary = json.loads(llm.content_to_text(getattr(m, "content", "")))
            except (json.JSONDecodeError, TypeError):
                return {}
            return summary if isinstance(summary, dict) else {}
    return {}


class AgentBI:
    """`create_agent` 自主路线的会话（持久化 + 可中断，与 `ConversationBI` 同形）。"""

    def __init__(self, parser: DBParser, *, human_review: bool = False,
                 thread_id: str | None = None, checkpointer: Any = None, verbose: bool = False,
                 lang: str = "zh"):
        self.parser = parser
        self.thread_id = thread_id or new_thread_id("agent")
        self.checkpointer = checkpointer or make_checkpointer()
        self.human_review = human_review
        self.verbose = verbose
        self.lang = lang
        # middleware 挂 HITL 后必须配 checkpointer —— 图要能在 interrupt() 处暂停并存档。
        self.graph = build_autonomous_agent(
            parser,
            middleware=build_agent_middleware(human_review=human_review),
            checkpointer=self.checkpointer,
            lang=lang,
        )

    # ------------------------------------------------------------------ #
    # 便利属性（与 ConversationBI 对齐）
    # ------------------------------------------------------------------ #

    @property
    def config(self) -> dict:
        return thread_config(self.thread_id)

    @property
    def _messages(self) -> list:
        snap = self.graph.get_state(self.config)
        return list((snap.values or {}).get("messages", []))

    @property
    def turns(self) -> list:
        """(用户问题, 助手回答) 历史——从 agent 的消息轨迹里还原。

        agent 一轮里可能夹着多次工具调用，这里只取「每条用户消息 → 其后最后一段有内容的
        AI 文本」，与 `ConversationBI.turns` 的语义一致。
        """
        turns: list = []
        pending: str | None = None
        for m in self._messages:
            kind = getattr(m, "type", None)
            if kind == "human":
                pending = llm.content_to_text(getattr(m, "content", "")).strip()
            elif kind == "ai" and pending is not None:
                text = llm.content_to_text(getattr(m, "content", "")).strip()
                if text:
                    turns.append((pending, text))
                    pending = None
        return turns

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #

    def ask(self, question: str, show_trace: bool = False) -> dict:
        """发起一轮提问；若 `HumanInTheLoopMiddleware` 拦下 `run_sql` 则返回暂停载荷。"""
        result = self.graph.invoke(
            {"messages": [{"role": "user", "content": question}]}, self.config,
        )
        return self._to_trace(result, raw=question)

    def resume(self, value: Any, show_trace: bool = False) -> dict:
        """把人工决定送回暂停中的图。

        `value` 用**框架原生格式**：`{"decisions": [{"type": "approve" | "edit" | "reject",
        ...}, ...]}`（每个被拦下的工具调用一项）。
        """
        result = self.graph.invoke(Command(resume=value), self.config)
        return self._to_trace(result, raw=None)

    def _to_trace(self, result: dict, raw: str | None) -> dict:
        payload = pending_interrupt(result)
        messages = result.get("messages", []) if isinstance(result, dict) else []
        summary = _last_sql_summary(messages)
        trace = {
            "route": "agent",
            "raw_question": raw if raw is not None else self._last_human(messages),
            "standalone_question": None,
            "scope": None,
            # `sql` 优先取真正执行的 `run_sql` 参数；若模型只起草未执行，则回填子代理摘要里的草稿
            "sql": _last_run_sql(messages) or summary.get("sql"),
            # 批 5：子代理的试跑次数（未走 `write_sql` 时为 0），供成本快照与审计对比
            "attempts": int(summary.get("attempts") or 0),
            "columns": None,
            "rows": None,
            "answer": _last_ai_text(messages) or None,
            "status": "interrupted" if payload is not None else "done",
        }
        if payload is not None:
            trace["interrupt"] = payload
        return trace

    @staticmethod
    def _last_human(messages: list) -> str | None:
        for m in reversed(list(messages)):
            if getattr(m, "type", None) == "human":
                return llm.content_to_text(getattr(m, "content", "")).strip()
        return None
