from __future__ import annotations

from dataclasses import replace

import pytest

from sql_agent.agent import SQLAgent
from sql_agent.config import settings as default_settings
from sql_agent.llm import extract_json
from sql_agent.trace_store import NullTraceStore


class FakeLLM:
    model = "fake-model"

    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[str] = []

    def _reply(self, step: str) -> str:
        self.calls.append(step)
        if step not in self.responses:
            raise AssertionError(f"未配置 step={step} 的响应")
        return self.responses[step]

    def chat(
        self,
        messages,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        budget=None,
        step: str = "",
    ) -> str:
        return self._reply(step)

    def chat_json(
        self,
        messages,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        budget=None,
        step: str = "",
    ) -> dict:
        return extract_json(self._reply(step))

    def chat_structured(
        self,
        messages,
        schema,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        budget=None,
        step: str = "",
    ) -> dict:
        # 测试桩：忽略 schema，直接走 JSON 解析，模拟结构化输出成功
        return self.chat_json(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            budget=budget,
            step=step,
        )


SELECTOR_INVOICES = (
    '{"tables": ["invoices"], "reason": "发票统计", "confidence": "high"}'
)


def make_agent(responses: dict[str, str], tmp_path) -> tuple[SQLAgent, FakeLLM]:
    fake = FakeLLM(responses)
    settings = replace(
        default_settings,
        trace_dir=tmp_path / "traces",
        feedback_db=tmp_path / "feedback.db",
    )
    agent = SQLAgent(llm=fake, settings=settings, trace_store=NullTraceStore())
    return agent, fake


def test_agent_success_end_to_end(tmp_path) -> None:
    agent, fake = make_agent(
        {
            "selector": SELECTOR_INVOICES,
            "generator": "```sql\nSELECT COUNT(*) AS invoice_count FROM invoices;\n```",
            "summarizer": (
                '{"summary": "共有 412 张发票。", '
                '"claims": [{"text": "共有 412 张发票", "value": 412, '
                '"source": "row[0].invoice_count"}]}'
            ),
        },
        tmp_path,
    )
    result = agent.run("数据库里一共有多少张发票？")
    assert result.status == "ok", result.error
    assert result.success
    assert result.rows == [(412,)]
    assert result.row_count == 1
    assert result.tables == ["invoices"]
    assert result.answer == "共有 412 张发票。"
    assert result.grounding_ok
    assert result.claims and result.claims[0]["value"] == 412
    assert result.contract is not None
    assert result.contract.status == "ok"
    assert result.trace_id.startswith("tr_")
    assert fake.calls == ["selector", "generator", "summarizer"]


def test_agent_soft_empty_on_out_of_range(tmp_path) -> None:
    agent, fake = make_agent(
        {
            "selector": SELECTOR_INVOICES,
            "generator": "-- CANNOT_ANSWER",
        },
        tmp_path,
    )
    result = agent.run("2015年的销售额是多少？")
    assert result.status == "empty"
    assert result.empty_reason == "out_of_range"
    assert result.executed
    assert result.sql == ""
    assert result.attempts == 0
    assert "2013-12-22" in result.answer
    assert "summarizer" not in fake.calls
    assert fake.calls == ["selector", "generator"]


def test_agent_redline_blocks_without_retry(tmp_path) -> None:
    agent, fake = make_agent(
        {
            "selector": SELECTOR_INVOICES,
            "generator": "```sql\nDROP TABLE invoices\n```",
        },
        tmp_path,
    )
    result = agent.run("有多少张发票？")
    assert result.status == "error"
    assert result.error_code == "E5003"
    assert result.attempts == 1
    assert "rewriter" not in fake.calls


def test_agent_rewrite_recovers(tmp_path) -> None:
    agent, fake = make_agent(
        {
            "selector": SELECTOR_INVOICES,
            "generator": "```sql\nSELECT COUNT(*) AS n FROM invoicez\n```",
            "rewriter": "```sql\nSELECT COUNT(*) AS n FROM invoices\n```",
            "summarizer": (
                '{"summary": "共有 412 张发票。", '
                '"claims": [{"text": "共有 412 张发票", "value": 412, '
                '"source": "row[0].n"}]}'
            ),
        },
        tmp_path,
    )
    result = agent.run("有多少张发票？")
    assert result.status == "ok", result.error
    assert result.attempts == 2
    assert "FROM invoices" in result.sql
    assert fake.calls == ["selector", "generator", "rewriter", "summarizer"]


def test_agent_meta_refusal_skips_llm(tmp_path) -> None:
    agent, fake = make_agent({}, tmp_path)
    result = agent.run("数据库里有哪些表？")
    assert result.status == "error"
    assert result.error_code == "E1003"
    assert "Chinook" in result.answer
    assert fake.calls == []
    assert result.intent is not None
    assert result.intent["intent"] == "meta"


def test_agent_undefined_metric_skips_llm(tmp_path) -> None:
    agent, fake = make_agent({}, tmp_path)
    result = agent.run("客单价是多少？")
    assert result.status == "error"
    assert result.error_code == "E7005"
    assert "客单价" in result.answer
    assert fake.calls == []


def test_agent_empty_input(tmp_path) -> None:
    agent, fake = make_agent({}, tmp_path)
    result = agent.run("   ")
    assert result.status == "error"
    assert result.error_code == "E1001"
    assert fake.calls == []


def test_agent_question_too_long(tmp_path) -> None:
    agent, fake = make_agent({}, tmp_path)
    result = agent.run("销售额" * 200)
    assert result.status == "error"
    assert result.error_code == "E1002"
    assert fake.calls == []
