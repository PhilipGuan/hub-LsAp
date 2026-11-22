"""会话图的使用门面：`ConversationBI`。

对应 `chatbi.ChatBI` 的「进阶版」——
- 记忆不再放内存 list，而是随 `thread_id` 落在 LangGraph 检查点里；
- `ask()` 遇到 `interrupt()`（澄清 / 人工复核）会返回 `status="interrupted"`，
  由调用方 `.resume(...)` 续接，或直接交给 `hitl.review_loop` 跑完。
"""

from __future__ import annotations

from typing import Any

from langgraph.types import Command

from ..db import DBParser
from ..hitl import pending_interrupt
from ..session import make_checkpointer, new_thread_id, thread_config
from .graph import build_conversation_graph


class ConversationBI:
    """持久化 + 可中断的多轮 BI 会话（LangGraph 会话图）。"""

    def __init__(self, parser: DBParser, *, thread_id: str | None = None,
                 checkpointer: Any = None, checkpointer_kind: str = "memory",
                 checkpointer_path: str = "checkpoints.sqlite",
                 human_review: bool = False, verbose: bool = False, lang: str = "zh"):
        self.parser = parser
        self.thread_id = thread_id or new_thread_id("chat")
        self.checkpointer = checkpointer or make_checkpointer(checkpointer_kind, checkpointer_path)
        self.human_review = human_review
        self.verbose = verbose
        self.lang = lang
        self.graph = build_conversation_graph(
            parser, human_review=human_review, verbose=verbose, checkpointer=self.checkpointer,
            lang=lang,
        )

    # ------------------------------------------------------------------ #
    # 便利属性
    # ------------------------------------------------------------------ #

    @property
    def config(self) -> dict:
        return thread_config(self.thread_id)

    @property
    def turns(self) -> list:
        """当前线程已沉淀的 (问题, 回答) 历史（读自检查点，不依赖进程内存）。

        注意：经过检查点序列化，元组会被还原成列表，这里统一转回元组方便使用。
        """
        snap = self.graph.get_state(self.config)
        return [tuple(t) for t in (snap.values or {}).get("turns", [])]

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #

    def ask(self, question: str, show_trace: bool = False) -> dict:
        """发起一轮提问；若图暂停（澄清/复核）则返回 `status="interrupted"`。"""
        result = self.graph.invoke({"raw_question": question}, self.config)
        return self._to_trace(result, raw=question)

    def resume(self, value: Any, show_trace: bool = False) -> dict:
        """把用户答复/人工决定送回暂停中的图，继续跑。"""
        result = self.graph.invoke(Command(resume=value), self.config)
        return self._to_trace(result, raw=None)

    def _to_trace(self, result: dict, raw: str | None) -> dict:
        payload = pending_interrupt(result)
        trace = {
            "raw_question": raw if raw is not None else result.get("raw_question"),
            "standalone_question": result.get("question"),
            "scope": result.get("scope"),
            "tables": result.get("tables"),
            "sql": result.get("sql"),
            "attempts": result.get("attempts", 0),
            "ok": bool(result.get("ok")),
            "columns": result.get("columns"),
            "rows": result.get("rows"),
            "answer": result.get("answer"),
            "status": "interrupted" if payload is not None else "done",
        }
        if payload is not None:
            trace["interrupt"] = payload
        return trace
