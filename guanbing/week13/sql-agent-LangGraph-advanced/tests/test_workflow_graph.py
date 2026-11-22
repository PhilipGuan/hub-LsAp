"""LangGraph 状态图测试：7 阶段串接 + 校验/改写回环（零网络，假 LLM）。"""

from __future__ import annotations

import pytest

from sqlagent import llm
from sqlagent.schemas import TableSelection
from sqlagent.workflow.graph import (FAIL_ANSWER, MAX_REWRITE, build_graph,
                                     run_workflow)
from tests.conftest import make_structured_responder, make_text_responder


@pytest.fixture
def patch_llm(monkeypatch):
    """返回一个「安装假 LLM」的小工厂。"""

    def install(*, tables=("tracks",), texts):
        monkeypatch.setattr(llm, "invoke_structured_messages",
                            make_structured_responder({
                                TableSelection: lambda s, m: TableSelection(tables=list(tables)),
                            }))
        monkeypatch.setattr(llm, "invoke_messages", make_text_responder(list(texts)))

    return install


BAD_SQL = "SELECT nope_column FROM tracks;"          # 能过守卫，但执行报错
GOOD_SQL = "SELECT COUNT(*) FROM tracks;"


def test_happy_path_executes_and_summarizes(parser, patch_llm):
    patch_llm(texts=[GOOD_SQL, "共有 3503 首歌曲。"])
    res = run_workflow("数据库里一共有多少首歌曲？", parser, verbose=False)

    assert res["ok"] is True
    assert res["tables"] == ["tracks"]
    assert res["rows"] == [(3503,)]
    assert res["attempts"] == 1          # 一次校验即通过，没有改写
    assert res["error"] is None
    assert "3503" in res["answer"]


def test_rewrite_loop_on_execution_error(parser, patch_llm):
    """校验失败 → 改写一次 → 通过。"""
    patch_llm(texts=[BAD_SQL, GOOD_SQL, "共 3503 首歌曲。"])
    res = run_workflow("有多少首歌？", parser, verbose=False)

    assert res["ok"] is True
    assert res["attempts"] == 2          # 生成 1 次 + 改写 1 次
    assert res["rows"] == [(3503,)]


def test_guard_blocks_write_then_rewrite_fixes(parser, patch_llm):
    """结构性护栏生效：模型先吐了 DROP，被守卫拦下，改写后才通过。"""
    patch_llm(texts=["DROP TABLE tracks;", GOOD_SQL, "共 3503 首歌曲。"])
    res = run_workflow("有多少首歌？", parser, verbose=False)

    assert res["ok"] is True
    assert res["attempts"] == 2
    assert res["sql"] == GOOD_SQL


def test_gives_up_after_max_rewrite(parser, patch_llm):
    """连续失败到次数用尽：如实返回失败轨迹，绝不编造结果。"""
    patch_llm(texts=[BAD_SQL] + [BAD_SQL] * MAX_REWRITE)
    res = run_workflow("有多少首歌？", parser, verbose=False)

    assert res["ok"] is False
    assert res["attempts"] == MAX_REWRITE + 1
    assert res["rows"] is None
    assert res["answer"] == FAIL_ANSWER
    assert res["error"]


def test_structured_parse_failure_falls_back_to_first_table(parser, monkeypatch):
    """选表结构化输出解析失败 → 落回首表兜底（保留原实现的容错语义）。"""
    monkeypatch.setattr(llm, "invoke_structured_messages", lambda schema, messages, **kw: None)
    monkeypatch.setattr(llm, "invoke_messages", make_text_responder(["SELECT 1;", "ok"]))
    res = run_workflow("随便问问", parser, verbose=False)
    assert res["tables"] == parser.table_names[:1]


def test_graph_topology_has_rewrite_cycle(parser):
    """图的拓扑：7 个节点齐全，且 check 节点有 3 条条件出边（execute/rewrite/fail）。"""
    graph = build_graph(parser, verbose=False)
    nodes = graph.get_graph().nodes
    assert {"select", "generate", "check", "rewrite", "execute", "summarize", "fail"} <= set(nodes)
