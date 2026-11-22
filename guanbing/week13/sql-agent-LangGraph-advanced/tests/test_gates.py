"""三道闸门测试：[A] 追问改写 / [B] 范围判定（含结构兜底）/ [C] 三分支。"""

from __future__ import annotations

import pytest

from sqlagent import llm
from sqlagent.chatbi import ChatBI, scope_check
from sqlagent.schemas import ScopeJudgement, TableSelection
from tests.conftest import make_structured_responder, make_text_responder


def _scope_only(monkeypatch, judgement):
    monkeypatch.setattr(llm, "invoke_structured_messages",
                        lambda schema, messages, **kw: judgement)


def test_rewrite_followup_without_history_is_noop(parser, monkeypatch):
    """无历史时不该调用模型，原样返回。"""
    monkeypatch.setattr(llm, "invoke_messages",
                        make_text_responder([]))  # 一旦被调用就会抛错
    from sqlagent.chatbi import rewrite_followup
    assert rewrite_followup("有多少？", []) == "有多少？"


def test_scope_in_with_valid_table(parser, monkeypatch):
    _scope_only(monkeypatch, ScopeJudgement(scope="in", reason="统计歌曲", tables=["tracks"]))
    out = scope_check("有多少首歌？", parser)
    assert out["scope"] == "in" and out["tables"] == ["tracks"]


def test_scope_structural_fallback_downgrades_to_ambiguous(parser, monkeypatch):
    """声称 in 却落不到任何真实表 → 强制降级为 ambiguous（结构性兜底）。"""
    _scope_only(monkeypatch, ScopeJudgement(scope="in", reason="", tables=["not_a_table"]))
    out = scope_check("随便问问", parser)
    assert out["scope"] == "ambiguous"
    assert out["tables"] == []
    assert out["clarify"]


def test_scope_parse_failure_is_ambiguous(parser, monkeypatch):
    _scope_only(monkeypatch, None)  # 模拟结构化解析失败
    out = scope_check("随便问问", parser)
    assert out["scope"] == "ambiguous"
    assert "解析" in out["reason"]


def test_chatbi_out_branch_never_generates_sql(parser, monkeypatch):
    _scope_only(monkeypatch, ScopeJudgement(scope="out", reason="与数据库无关"))
    monkeypatch.setattr(llm, "invoke_messages", make_text_responder([]))  # 不该有任何文本调用

    res = ChatBI(parser).ask("明天天气怎么样？")
    assert res["scope"]["scope"] == "out"
    assert res["sql"] is None and res["rows"] is None
    assert "超出了当前 chinook 数据库的范围" in res["answer"]


def test_chatbi_ambiguous_branch_asks_back(parser, monkeypatch):
    _scope_only(monkeypatch, ScopeJudgement(scope="ambiguous", clarify="你指的是哪张表？"))
    monkeypatch.setattr(llm, "invoke_messages", make_text_responder([]))

    res = ChatBI(parser).ask("有多少？")
    assert res["sql"] is None
    assert "你指的是哪张表？" in res["answer"]


def test_chatbi_in_branch_runs_workflow(parser, monkeypatch):
    monkeypatch.setattr(llm, "invoke_structured_messages", make_structured_responder({
        ScopeJudgement: lambda s, m: ScopeJudgement(scope="in", reason="ok", tables=["tracks"]),
        TableSelection: lambda s, m: TableSelection(tables=["tracks"]),
    }))
    monkeypatch.setattr(llm, "invoke_messages",
                        make_text_responder(["SELECT COUNT(*) FROM tracks;", "共有 3503 首歌曲。"]))
    res = ChatBI(parser).ask("数据库里一共有多少首歌曲？")
    assert res["ok"] is True
    assert res["rows"] == [(3503,)]


def test_chatbi_history_is_trimmed(parser, monkeypatch):
    """第 2 轮起会触发追问改写（[A] 闸门），故假 LLM 需准备 3 次文本回复。"""
    _scope_only(monkeypatch, ScopeJudgement(scope="out", reason="x"))
    monkeypatch.setattr(llm, "invoke_messages",
                        make_text_responder(["问题1", "问题2", "问题3"]))

    bot = ChatBI(parser, max_history=2)
    for i in range(4):
        bot.ask(f"问题{i}")
    assert len(bot.history) == 2
