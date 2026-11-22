"""时间旅行 + 人工介入驱动测试（零网络，假 LLM）。

- `list_hops` / `format_hops` / `checkpoint_before` / `replay_from`：检查点链与回放；
- `review_loop` / `pending_interrupt`：把「暂停 → 问人 → 续跑」封装成的小驱动器。
"""

from __future__ import annotations

import pytest

from sqlagent import llm
from sqlagent.hitl import pending_interrupt, review_loop
from sqlagent.schemas import TableSelection
from sqlagent.session import make_checkpointer, thread_config
from sqlagent.time_travel import (checkpoint_before, format_hops, list_hops,
                                  replay_from)
from sqlagent.workflow.graph import build_checkpointed_graph
from tests.conftest import make_structured_responder, make_text_responder

GOOD_SQL = "SELECT COUNT(*) FROM tracks;"

INITIAL = {"question": "数据库里一共有多少首歌曲？", "attempts": 0, "ok": False,
           "error": None, "columns": None, "rows": None, "answer": None}


@pytest.fixture
def patch_llm(monkeypatch):
    def install(texts):
        monkeypatch.setattr(llm, "invoke_structured_messages",
                            make_structured_responder({
                                TableSelection: lambda s, m: TableSelection(tables=["tracks"]),
                            }))
        monkeypatch.setattr(llm, "invoke_messages", make_text_responder(list(texts)))
    return install


# --------------------------------------------------------------------------- #
# 轨迹（list_hops / format_hops / checkpoint_before）
# --------------------------------------------------------------------------- #

def test_list_hops_shows_full_trajectory(parser, patch_llm):
    patch_llm([GOOD_SQL, "共有 3503 首歌曲。"])
    graph = build_checkpointed_graph(parser, checkpointer=make_checkpointer(), verbose=False)
    cfg = thread_config("tt-hops")
    graph.invoke(INITIAL, cfg)

    hops = list_hops(graph, cfg)
    assert len(hops) >= 5                                   # select/generate/check/execute/summarize...
    assert hops[0]["step"] >= hops[-1]["step"]              # 最新在前
    assert any(h.get("sql") == GOOD_SQL for h in hops)

    text = format_hops(hops)
    assert "共" in text and "SQL:" in text

    # 找到「下一跳将进入 execute」的检查点（回放要用它）
    assert checkpoint_before(graph, cfg, "execute")


# --------------------------------------------------------------------------- #
# 回放（replay_from）——从某一跳分叉、改点状态、继续跑
# --------------------------------------------------------------------------- #

def test_replay_from_forks_and_overrides_sql(parser, patch_llm):
    # 首次运行用 2 条文本（生成 + 汇总）；回放会再调一次汇总 → 第 3 条
    patch_llm([GOOD_SQL, "首次回答。", "回放回答。"])
    graph = build_checkpointed_graph(parser, checkpointer=make_checkpointer(), verbose=False)
    cfg = thread_config("tt-replay")
    first = graph.invoke(INITIAL, cfg)
    assert first["rows"] == [(3503,)]

    cid = checkpoint_before(graph, cfg, "execute")
    assert cid

    expected_rows = parser.execute_sql("SELECT COUNT(*) FROM albums;")[1]
    final = replay_from(graph, cfg, cid, updates={"sql": "SELECT COUNT(*) FROM albums;"})

    assert final["rows"] == expected_rows          # 换了 SQL → 换了结果
    assert final["answer"] == "回放回答。"


# --------------------------------------------------------------------------- #
# 人工介入驱动（review_loop / pending_interrupt）
# --------------------------------------------------------------------------- #

def test_pending_interrupt_is_none_on_completed_result():
    assert pending_interrupt({"answer": "ok"}) is None


def test_review_loop_approve_runs_to_completion(parser, patch_llm):
    patch_llm([GOOD_SQL, "共有 3503 首歌曲。"])
    graph = build_checkpointed_graph(parser, checkpointer=make_checkpointer(),
                                     human_review=True, verbose=False)
    cfg = thread_config("rl-approve")

    final = review_loop(graph, INITIAL, cfg, reviewer=lambda payload: {"action": "approve"})

    assert final["rows"] == [(3503,)]
    assert final["answer"] == "共有 3503 首歌曲。"


def test_review_loop_can_reject(parser, patch_llm):
    patch_llm([GOOD_SQL])
    graph = build_checkpointed_graph(parser, checkpointer=make_checkpointer(),
                                     human_review=True, verbose=False)
    cfg = thread_config("rl-reject")

    seen = {}

    def reviewer(payload):
        seen.update(payload)
        return {"action": "reject", "note": "口径不对"}

    final = review_loop(graph, INITIAL, cfg, reviewer=reviewer)

    assert seen["type"] == "sql_review" and seen["sql"] == GOOD_SQL
    assert final["ok"] is False
    assert final["rows"] is None
