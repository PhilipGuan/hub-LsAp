from __future__ import annotations

import pytest

from sql_agent.trace_store import (
    FeedbackStore,
    JsonlTraceStore,
    normalize_user_code,
)


def test_normalize_user_code() -> None:
    assert normalize_user_code(None) == "local"
    assert normalize_user_code("") == "local"
    assert normalize_user_code("pilot-01") == "pilot-01"
    with pytest.raises(ValueError):
        normalize_user_code("bad code!")


def test_feedback_store_upsert(tmp_path) -> None:
    store = FeedbackStore(tmp_path / "feedback.db")
    store.save(
        trace_id="tr_1", user_code="u1", rating=1, question="有多少张发票？"
    )
    store.save(
        trace_id="tr_1", user_code="u1", rating=-1, reason="数字不对"
    )
    items = store.list()
    assert len(items) == 1
    assert items[0]["rating"] == -1
    assert items[0]["reason"] == "数字不对"
    summary = store.summary()
    assert summary == {"total": 1, "up": 0, "down": 1, "positive_rate": 0.0}


def test_feedback_store_rejects_bad_rating(tmp_path) -> None:
    store = FeedbackStore(tmp_path / "feedback.db")
    with pytest.raises(ValueError):
        store.save(trace_id="tr_1", user_code="u1", rating=0)


def test_jsonl_trace_store(tmp_path) -> None:
    store = JsonlTraceStore(tmp_path / "traces", retention_days=30)
    assert store.write({"trace_id": "tr_a", "question": "q1"})
    assert store.write({"trace_id": "tr_b", "question": "q2"})
    assert store.read("tr_a")["question"] == "q1"
    assert store.read("missing") is None
    recent = store.recent(limit=1)
    assert len(recent) == 1
    assert recent[0]["trace_id"] == "tr_b"
