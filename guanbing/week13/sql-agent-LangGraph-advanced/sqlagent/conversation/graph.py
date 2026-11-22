"""进阶会话图：把「三道闸门」也纳入 LangGraph，让整轮对话成为**一张可暂停、可持久化**的图。

与 `chatbi.ChatBI`（内存版、纯 Python 编排）的差异，正是本次进阶要演示的三点：

1. **持久化**：整张图挂 `checkpointer`，多轮记忆（`turns`）随 `thread_id` 落到检查点库；
2. **澄清续接**：`ambiguous` 分支改为 `interrupt()` 暂停等用户回答，`Command(resume=...)`
   续接 → 消灭原版「澄清只能单轮」的限制；
3. **人工介入**：SQL 子图内可选 `human_review` 闸门，其 `interrupt()` 会一路冒泡到本图外部。

拓扑（父图）：

```
START → summarize → rewrite → scope ──in────→ sql(子图) ─→ finalize → END
                                │  ├─out──────→ reject   ─→ finalize → END
                                │  └─ambiguous→ clarify ─┬→ rewrite   （澄清续接回环，
                                                         │             最多 MAX_CLARIFY 次）
                                                         └→ finalize  （澄清超限，给话术收尾）
```
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .. import i18n
from ..db import DBParser
from ..gates import (
    REWRITE_WINDOW,
    SUMMARY_TRIGGER,
    rewrite_followup,
    scope_check,
    summarize_history,
)
from ..workflow.graph import build_graph

# D11：同一轮里最多反问他几次。用尽后不再无限暂停，而是给出可读话术收尾——
# 「用户怎么答都答不到点子上」时，`interrupt()` 的回环本身就会变成一种挂死。
MAX_CLARIFY = 3

# 默认语言（zh）下的澄清超限话术。英文版在 `i18n`（`i18n.text("clarify_limit_answer", lang)`）；
# 保留这个模块级常量，避免打断既有导入路径。
CLARIFY_LIMIT_ANSWER = i18n.text("clarify_limit_answer", "zh")


class ConversationState(TypedDict, total=False):
    """会话状态：会话层字段 + **复用 SQL 子图的同名字段**。

    同名字段（question/tables/sql/...）是有意为之：SQL 子图直接以 `add_node("sql", ...)`
    挂进来，父图把状态透传给子图，子图写回的结果再并回父图——无需手工搬运。
    """

    # —— 会话层 ——
    raw_question: str                       # 用户本轮原始输入
    question: str                           # 改写后的自包含问题（子图也读这个键）
    scope: dict | None
    clarify_count: int                      # 本轮已反问次数（见 MAX_CLARIFY；收尾时归零）
    turns: Annotated[list, operator.add]    # 追加式对话历史：[(用户问题, 助手回答)]
    history_summary: str                    # 更早对话的滚动摘要（见 node_summarize）
    summarized_upto: int                    # 已被压进 summary 的 `turns` 前缀长度
    # —— 透传自 SQL 子图 ——
    tables: list
    sql: str
    attempts: int
    ok: bool
    error: str | None
    columns: list | None
    rows: list | None
    answer: str | None
    review_action: str | None


def build_conversation_graph(parser: DBParser, *, human_review: bool = False,
                             verbose: bool = True, checkpointer: Any = None,
                             lang: str = "zh") -> Any:
    """构建并编译会话图。

    Args:
        human_review: 为 True 时，SQL 子图内插入人工复核闸门（其 `interrupt()` 冒泡到本图）。
        checkpointer: 传入后启用持久化；`None` 则图不可暂停（无法用 interrupt）。
        lang: 面向用户话术与结论的语言（`zh` / `en`）；只影响文案，不影响拓扑 / 调用次数。
    """

    sql_subgraph = build_graph(parser, human_review=human_review, verbose=verbose, lang=lang)

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    def node_summarize(state: ConversationState) -> dict:
        """[S] 历史滚动摘要：把「较早的对话」压成一段背景，取代「只看最近 5 轮」的硬截断。

        只在「尚未摘要的轮数 > `SUMMARY_TRIGGER`」时才真正调用一次 LLM，把除最近
        `REWRITE_WINDOW` 轮之外的较早对话并进 `history_summary`；短对话直接返回空
        update。因此**默认路径的 LLM 调用次数不变**（DESIGN.md §4 的不变量）。
        """
        turns = state.get("turns") or []
        done = state.get("summarized_upto") or 0
        if len(turns) - done <= SUMMARY_TRIGGER:
            return {}
        keep = len(turns) - REWRITE_WINDOW
        summary = summarize_history(state.get("history_summary") or "", turns[done:keep],
                                    lang=lang)
        log(f"[S] 历史摘要：压缩 {keep - done} 轮，保留最近 {REWRITE_WINDOW} 轮原文")
        return {"history_summary": summary, "summarized_upto": keep}

    def node_rewrite(state: ConversationState) -> dict:
        """[A] 追问改写；同时**重置本轮字段**，避免上一轮结果串味。"""
        history = state.get("turns") or []
        standalone = rewrite_followup(state["raw_question"], history,
                                      state.get("history_summary") or "", lang=lang)
        log(f"[A] 独立问题：{standalone}")
        return {
            "question": standalone,
            "tables": None, "sql": None, "attempts": 0, "ok": False,
            "error": None, "columns": None, "rows": None, "answer": None,
            "review_action": None, "scope": None,
        }

    def node_scope(state: ConversationState) -> dict:
        """[B] 范围判定。"""
        scope = scope_check(state["question"], parser, lang=lang)
        log(f"[B] 范围判定：{scope['scope']}（{scope['reason']}）")
        return {"scope": scope}

    def route_after_scope(state: ConversationState) -> str:
        return {"in": "sql", "out": "reject", "ambiguous": "clarify"}.get(
            (state.get("scope") or {}).get("scope"), "clarify",
        )

    def node_clarify(state: ConversationState) -> dict:
        """[C-ambiguous] 用 `interrupt()` 反问，等用户回答后续接（最多 `MAX_CLARIFY` 次）。

        这是「澄清续接」的关键：图在此暂停，用户下一条消息通过 `Command(resume=...)`
        送回，作为新的原始问题重新走 [A]/[B]，从而真正实现「反问 → 等待 → 续接」。

        额度用尽则**不再暂停**：直接给出可读话术收尾（D11），否则就成了另一种挂死。
        """
        count = (state.get("clarify_count") or 0) + 1
        if count > MAX_CLARIFY:
            log(f"[C] 澄清已达上限（{MAX_CLARIFY} 次），放弃并收尾")
            return {"clarify_count": count, "answer": i18n.text("clarify_limit_answer", lang),
                    "ok": False}

        scope = state.get("scope") or {}
        text = interrupt({
            "type": "clarify",
            "question": scope.get("clarify") or i18n.text("clarify_fallback", lang),
            "reason": scope.get("reason"),
        })
        log(f"[C] 用户澄清：{text}")
        return {"raw_question": (text or "").strip(), "clarify_count": count}

    def node_reject(state: ConversationState) -> dict:
        """[C-out] 直接谢绝，不生成任何 SQL。"""
        reason = (state.get("scope") or {}).get("reason") or ""
        return {"answer": i18n.text("out_of_scope", lang).format(reason=reason)}

    def node_finalize(state: ConversationState) -> dict:
        """收尾：把本轮 (问题, 回答) 追加进 `turns`，并**清零本轮的澄清计数**。

        清零放在这里而不是 `node_rewrite`：`clarify → rewrite` 是同一轮内的回环，
        在 `rewrite` 里重置会把计数抹掉、上限永远够不到；而每轮都必过 `finalize`，
        所以「下一轮重新计数」由它负责。
        """
        return {"turns": [(state.get("raw_question"), state.get("answer"))],
                "clarify_count": 0}

    def route_after_clarify(state: ConversationState) -> str:
        """澄清回环的出口：还有额度回 [A] 重问，用尽则收尾（不经过 `rewrite`）。"""
        return "finalize" if (state.get("clarify_count") or 0) > MAX_CLARIFY else "rewrite"

    g = StateGraph(ConversationState)
    g.add_node("summarize", node_summarize)
    g.add_node("rewrite", node_rewrite)
    g.add_node("scope", node_scope)
    g.add_node("clarify", node_clarify)
    g.add_node("sql", sql_subgraph)      # ← 子图：父图透传状态，无需手工搬运
    g.add_node("reject", node_reject)
    g.add_node("finalize", node_finalize)

    g.add_edge(START, "summarize")
    g.add_edge("summarize", "rewrite")
    g.add_edge("rewrite", "scope")
    g.add_conditional_edges("scope", route_after_scope,
                            {"sql": "sql", "reject": "reject", "clarify": "clarify"})
    # ← 澄清续接回环（唯一的第二个回环）：额度内回 [A] 重问，用尽直接去 finalize
    g.add_conditional_edges("clarify", route_after_clarify,
                            {"rewrite": "rewrite", "finalize": "finalize"})
    g.add_edge("sql", "finalize")
    g.add_edge("reject", "finalize")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer)
