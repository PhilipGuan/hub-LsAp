"""LangGraph 编排：状态图 + MemorySaver 多轮记忆。

        ┌─────────┐   positive   ┌───────────┐
        │ analyze │ ───────────▶ │ encourage │ ─┐
        └────┬────┘              └───────────┘  │
             │ negative        ┌───────────┐    │
             ├────────────────▶│  comfort  │ ───┼──▶ remember ──▶ END
             │ neutral         └───────────┘    │
             └────────────────▶│  neutral  │ ───┘
                                └───────────┘

- analyze / 三个回复节点通过队列调用外部进程1、进程2；
- MemorySaver 按 thread_id（即 session_id）保存 state，
  history 通过 reducer 累积，并随 SupportRequest 传给进程2 作为上下文；
- remember 节点在每轮结束时把 (user, assistant) 追加进记忆。

图本身运行在后端进程里，所以"图编排 + 进程隔离 + 多轮记忆"同时成立。
"""
from __future__ import annotations

from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

try:  # langgraph 新版本把 MemorySaver 重命名为 InMemorySaver
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:  # pragma: no cover
    from langgraph.checkpoint.memory import InMemorySaver as MemorySaver

from shared.protocol import (
    ReplyResult,
    SentimentRequest,
    SentimentResult,
    SupportRequest,
    new_rid,
)
from shared.runtime import call_stage, log

# 每个会话最多保留的历史轮数，避免记忆无限增长
_MAX_HISTORY_TURNS = 20


def _merge_history(
    current: list[dict[str, str]] | None,
    update: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    merged = list(current or []) + list(update or [])
    return merged[-_MAX_HISTORY_TURNS:]


class PipelineState(TypedDict, total=False):
    text: str
    history: Annotated[list[dict[str, str]], _merge_history]
    sentiment: str
    confidence: float
    reason: str
    style: str
    reply: str


def _build_graph(
    call_sentiment: Callable[[str], SentimentResult],
    call_support: Callable[[str, str, str, str, list[dict[str, str]]], ReplyResult],
):
    def analyze(state: PipelineState) -> dict[str, Any]:
        result = call_sentiment(state["text"])
        log(
            f"进程1 情感识别：{result.sentiment} "
            f"(confidence={result.confidence:.2f}, source={result.source})"
        )
        return {
            "sentiment": result.sentiment,
            "confidence": result.confidence,
            "reason": result.reason,
        }

    def make_responder(style: str):
        def respond(state: PipelineState) -> dict[str, Any]:
            history = [
                turn for turn in (state.get("history") or []) if isinstance(turn, dict)
            ]
            result = call_support(
                state["text"],
                state["sentiment"],
                state.get("reason", ""),
                style,
                history[-8:],
            )
            log(
                f"进程2 回复风格：{style} "
                f"(source={result.source}, history={len(history)} 轮)"
            )
            return {"reply": result.reply, "style": style}

        return respond

    def remember(state: PipelineState) -> dict[str, Any]:
        return {
            "history": [
                {
                    "user": state.get("text", ""),
                    "assistant": state.get("reply", ""),
                }
            ]
        }

    def route_sentiment(state: PipelineState) -> str:
        return {
            "positive": "encourage",
            "negative": "comfort",
        }.get(state.get("sentiment", ""), "neutral")

    graph = StateGraph(PipelineState)
    graph.add_node("analyze", analyze)
    graph.add_node("encourage", make_responder("encourage"))
    graph.add_node("comfort", make_responder("comfort"))
    graph.add_node("neutral", make_responder("neutral"))
    graph.add_node("remember", remember)

    graph.set_entry_point("analyze")
    graph.add_conditional_edges(
        "analyze",
        route_sentiment,
        {
            "encourage": "encourage",
            "comfort": "comfort",
            "neutral": "neutral",
        },
    )
    for node in ("encourage", "comfort", "neutral"):
        graph.add_edge(node, "remember")
    graph.add_edge("remember", END)

    return graph.compile(checkpointer=MemorySaver())


def build_orchestrator(
    sentiment_in: Any,
    sentiment_out: Any,
    support_in: Any,
    support_out: Any,
) -> Callable[[str, str], str]:
    """后端进程启动时调用：返回 orchestrator(text, session_id) -> reply。"""

    def call_sentiment(text: str) -> SentimentResult:
        request = SentimentRequest(rid=new_rid(), text=text)
        return call_stage(sentiment_in, sentiment_out, request, SentimentResult)

    def call_support(
        text: str,
        sentiment: str,
        reason: str,
        style: str,
        history: list[dict[str, str]],
    ) -> ReplyResult:
        request = SupportRequest(
            rid=new_rid(),
            text=text,
            sentiment=sentiment,  # type: ignore[arg-type]
            reason=reason,
            style=style,  # type: ignore[arg-type]
            history=history,
        )
        return call_stage(support_in, support_out, request, ReplyResult)

    compiled = _build_graph(call_sentiment, call_support)

    def run(text: str, session_id: str = "default") -> str:
        # 同一个 thread_id 共享一份 MemorySaver 记忆
        config = {"configurable": {"thread_id": session_id or "default"}}
        # history 传空列表：有 reducer 时会把空列表合并到已有历史上
        final_state = compiled.invoke({"text": text, "history": []}, config=config)
        reply = (final_state.get("reply") or "").strip()
        if not reply:
            raise RuntimeError("LangGraph 流程没有生成回复")
        return reply

    return run
