"""三道闸门里的两段纯逻辑：[A] 追问改写 / [B] 范围判定。

从 `chatbi.py` 拆出来，是为了让**进阶会话图**（`sqlagent.conversation`）复用它们时
不必连带 import `matplotlib` / `IPython`（那两个是 `chatbi.render_result` 的重依赖）。

`chatbi.py` 会 re-export 这三个名字，故 `from sqlagent.chatbi import scope_check`
依旧可用，行为不变。
"""

from __future__ import annotations

from . import i18n, llm, prompts
from .db import DBParser
from .schemas import ScopeJudgement
from .tools import table_catalog

# 默认语言（zh）下的越界谢绝话术。英文版在 `i18n`（见 `i18n.text("out_of_scope", lang)`）；
# 保留这个模块级常量是因为 `chatbi.py` 等旧路径 re-export 它，导入不可断。
OUT_OF_SCOPE_TEMPLATE = i18n.text("out_of_scope", "zh")

# —— 历史处理的两个口径（由 conversation/graph.py 的 node_summarize 使用）——
# REWRITE_WINDOW：追问改写时**逐字**附上的最近轮数（原实现就是 5，保持不变）；
# SUMMARY_TRIGGER：未摘要的轮数超过它才触发一次摘要；触发后会把「除最近
# REWRITE_WINDOW 轮之外」的较早对话压成滚动摘要。
# 两个常量合起来的效果：短对话完全不摘要（零额外 LLM 调用），长对话也不会无限增长。
REWRITE_WINDOW = 5
SUMMARY_TRIGGER = 8


def _history_text(turns: list, lang: str) -> str:
    """把 (问题, 回答) 列表摊成提示词里的对话历史（角色标签随语言走）。

    zh 下与改动前逐字一致（`用户：…\\n助手：…`），英文下换成 `User: …\\nAssistant: …`。
    """
    user = i18n.text("history_user_label", lang)
    assistant = i18n.text("history_assistant_label", lang)
    return "\n".join(f"{user}{q}\n{assistant}{a}" for q, a in turns)


def scope_check(question: str, parser: DBParser, lang: str = "zh") -> dict:
    """[B] 判断问题是否在数据库可答范围内：in / out / ambiguous。"""
    msgs = prompts.scope_prompt(lang).format_messages(
        catalog=table_catalog(parser), question=question,
    )
    judgement = llm.invoke_structured_messages(ScopeJudgement, msgs)

    if judgement is None:
        data = {"scope": "ambiguous", "reason": i18n.text("scope_unparsable", lang), "tables": [],
                "clarify": i18n.text("clarify_fallback", lang)}
    else:
        data = judgement.model_dump()

    data["tables"] = [t for t in (data.get("tables") or []) if t in parser.table_names]

    # 结构性兜底：声称 in 却落不到任何真实表 → 降级为 ambiguous，避免硬编 SQL
    if data["scope"] == "in" and not data["tables"]:
        data["scope"] = "ambiguous"
        data["reason"] = data["reason"] or i18n.text("scope_no_table_reason", lang)
        data["clarify"] = data["clarify"] or i18n.text("scope_no_table_clarify", lang)
    return data


def summarize_history(previous: str, turns: list, lang: str = "zh") -> str:
    """把较早的若干轮对话压成一段滚动摘要（可叠加在既有 `previous` 之上）。

    这是「摘要节点」背后的纯逻辑：会话变长后，与其把历史一刀切掉，不如把更早的事实压成
    一段背景一并带给改写器。`turns` 为空时原样返回既有摘要（不产生任何 LLM 调用）。
    """
    turns = list(turns or [])
    if not turns:
        return previous or ""
    turns_text = _history_text(turns, lang)
    msgs = prompts.summarize_history_prompt(lang).format_messages(
        previous=previous or i18n.text("history_empty", lang), turns=turns_text,
    )
    return llm.strip_code_fence(llm.invoke_messages(msgs)).strip()


def rewrite_followup(question: str, history: list, summary: str = "", lang: str = "zh") -> str:
    """[A] 把带上下文的追问改写成自包含的独立问题（无历史时原样返回）。

    `summary`（可选）是更早对话的滚动摘要；见 `conversation/graph.py` 的 `node_summarize`。
    不传时行为与改动前**逐字一致**（只看最近 `REWRITE_WINDOW` 轮）。
    """
    history = list(history or [])
    if not history and not summary:
        return question
    parts = []
    if summary:
        parts.append(f"（更早对话的摘要）\n{summary}" if lang != "en"
                     else f"(Summary of earlier turns)\n{summary}")
    if history:
        parts.append(_history_text(history[-REWRITE_WINDOW:], lang))
    msgs = prompts.rewrite_followup_prompt(lang).format_messages(
        history="\n\n".join(parts), question=question,
    )
    return llm.strip_code_fence(llm.invoke_messages(msgs)).strip()
