"""逐层裁判的**确定性**部分测试（L0/L1/L3/L4 + L5 数字溯源），全部离线。"""

from __future__ import annotations

from sqlagent.judge import JudgeAgent
from sqlagent.judge.deterministic import (judge_guard, judge_number_trace,
                                          judge_result, judge_scope, judge_tables,
                                          normalize_rows, row_equal)

ITEM_IN = {
    "id": "G1", "question": "数据库里一共有多少首歌曲？", "scope": "in",
    "tables": ["tracks"], "ref_sql": "SELECT COUNT(*) FROM tracks;",
    "ref_rows": [(3503,)], "ref_answer_keypoints": ["3503"], "tags": ["count"],
}
ITEM_OUT = {"id": "N1", "question": "明天天气？", "scope": "out", "tables": [],
            "ref_sql": None, "ref_rows": None, "ref_answer_keypoints": [], "tags": ["gate"]}


def test_judge_scope():
    assert judge_scope(ITEM_IN, {"scope": {"scope": "in"}})["pass"] is True
    bad = judge_scope(ITEM_IN, {"scope": {"scope": "out"}})
    assert bad["pass"] is False and "期望 in" in bad["detail"]


def test_judge_tables_missing_and_extra():
    assert judge_tables(ITEM_IN, {"tables": ["tracks"]})["pass"] is True
    missing = judge_tables(ITEM_IN, {"tables": ["albums"]})
    assert missing["pass"] is False and "缺表" in missing["detail"]
    extra = judge_tables(ITEM_IN, {"tables": ["tracks", "albums"]})
    assert extra["pass"] is True and "多选" in extra["detail"]   # 多选不判死，交 LLM 复核


def test_judge_tables_skips_gate_items():
    assert judge_tables(ITEM_OUT, {"tables": []})["pass"] is True


def test_judge_guard():
    assert judge_guard(ITEM_IN, {"ok": True})["pass"] is True
    failed = judge_guard(ITEM_IN, {"ok": False, "error": "no such column: nope"})
    assert failed["pass"] is False and "no such column" in failed["detail"]


def test_judge_result_strict_and_tolerance():
    assert judge_result(ITEM_IN, {"ok": True, "rows": [(3503,)]})["pass"] is True
    # 数值容差：3503.0000004 视为相等
    assert judge_result(ITEM_IN, {"ok": True, "rows": [(3503.0000004,)]})["pass"] is True
    # 行序无关
    item = {**ITEM_IN, "ref_rows": [("a", 1), ("b", 2)]}
    assert judge_result(item, {"ok": True, "rows": [("b", 2), ("a", 1)]})["pass"] is True
    # 执行失败 → 无结果可判
    assert judge_result(ITEM_IN, {"ok": False})["pass"] is False


def test_normalize_rows_helpers():
    assert row_equal([(1, "a")], [("a", 1)]) is False      # 列顺序不同 → 不相等（交 LLM 复核）
    assert normalize_rows(None) is None


def test_number_trace_flags_fabrication():
    rows = [("Jane Peacock", 21), ("Margaret Park", 20), ("Steve Johnson", 18)]
    item = {**ITEM_IN, "question": "每位客服各自负责多少位客户？", "ref_rows": rows}

    clean = judge_number_trace(item, {"rows": rows, "answer": "Jane Peacock 负责 21 位客户。"})
    assert clean["suspicious"] == []

    dirty = judge_number_trace(item, {"rows": rows, "answer": "共有 999 位客户。"})
    assert "999" in dirty["suspicious"]


def test_number_trace_exemptions():
    rows = [("Rock", 835), ("Latin", 386), ("Metal", 264)]
    item = {**ITEM_IN, "question": "销量最高的 3 个流派", "ref_rows": rows}

    # 名次豁免（1）与结果集数字豁免（835）
    out = judge_number_trace(item, {"rows": rows, "answer": "第 1 名 Rock 卖了 835 份。"})
    assert out["suspicious"] == []

    # 派生合计豁免：835 + 386 + 264 = 1485
    out = judge_number_trace(item, {"rows": rows, "answer": "合计 1485 份。"})
    assert out["suspicious"] == []


def test_judge_agent_without_llm_aggregates_deterministically(parser):
    judge = JudgeAgent(use_llm=False)
    trace = {"scope": {"scope": "in"}, "tables": ["tracks"], "ok": True,
             "rows": [(3503,)], "sql": "SELECT COUNT(*) FROM tracks;",
             "answer": "共有 3503 首歌曲。", "error": None}
    rep = judge.judge(ITEM_IN, trace)
    assert rep["pass"] is True
    # LLM 层被显式标记为「未给出结论」，而不是静默通过
    assert rep["layers"]["L2_sql"]["pass"] is None
    assert rep["layers"]["L5_answer"]["pass"] is None


def test_judge_agent_flags_failed_layer(parser):
    judge = JudgeAgent(use_llm=False)
    trace = {"scope": {"scope": "in"}, "tables": ["albums"], "ok": True,
             "rows": [(1,)], "sql": "SELECT 1;", "answer": "错了", "error": None}
    rep = judge.judge(ITEM_IN, trace)
    assert rep["pass"] is False
    assert rep["layers"]["L1_tables"]["pass"] is False
    assert rep["layers"]["L4_result"]["pass"] is False
