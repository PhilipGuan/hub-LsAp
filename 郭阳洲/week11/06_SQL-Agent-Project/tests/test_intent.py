from __future__ import annotations

import pytest

from sql_agent.intent import (
    ANALYTICS,
    AMBIGUOUS,
    CHITCHAT,
    META,
    OUT_OF_SCOPE,
    UNSUPPORTED,
    IntentGate,
)


@pytest.fixture(scope="module")
def gate():
    return IntentGate()


@pytest.mark.parametrize(
    "question",
    [
        "2010 年每个季度的销售额是多少？",
        "数据库里一共有多少张发票？",
        "每个流派的歌曲平均时长是多少秒？",
        "销售额最高的前 5 个国家是哪些？",
    ],
)
def test_analytics_questions_pass(gate: IntentGate, question: str) -> None:
    result = gate.rule_classify(question)
    assert result.is_analytics
    assert result.code == ""


@pytest.mark.parametrize(
    ("question", "intent", "code"),
    [
        ("删除所有客户", OUT_OF_SCOPE, "E1004"),
        ("修改客户邮箱", OUT_OF_SCOPE, "E1004"),
        ("帮我预测明年的销售额", UNSUPPORTED, "E1007"),
        ("为什么 2010 年销售额下降？", UNSUPPORTED, "E1007"),
        ("明年每个月的销售额是多少？", UNSUPPORTED, "E1007"),
        ("数据库里有哪些表？", META, "E1003"),
        ("你好", CHITCHAT, "E1003"),
        ("分析一下销售情况", AMBIGUOUS, "E1005"),
    ],
)
def test_refusals(gate: IntentGate, question: str, intent: str, code: str) -> None:
    result = gate.rule_classify(question)
    assert not result.is_analytics
    assert result.intent == intent
    assert result.code == code
    assert result.message


def test_metric_cue_rescues_ambiguous(gate: IntentGate) -> None:
    result = gate.rule_classify("销售额情况怎么样")
    assert result.is_analytics


def test_meta_answer(catalog) -> None:
    from sql_agent.intent import meta_answer

    text = meta_answer(catalog, "有哪些表？")
    assert "Chinook" in text
    assert "invoices" in text
    assert "数据截止" in text
