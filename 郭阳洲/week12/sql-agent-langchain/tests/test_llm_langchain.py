"""LangChain 版 LLM 门面的离线单元测试（注入 fake client，不产生真实调用）。"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import httpx
import openai
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from sql_agent.budget import Budget, BudgetExceeded
from sql_agent.llm import LLM, LLMError, extract_json


class SelectionOut(BaseModel):
    tables: list[str] = []


def _fake_client(*messages: AIMessage):
    return GenericFakeChatModel(messages=iter(list(messages)))


def test_chat_records_usage_and_cost() -> None:
    client = _fake_client(
        AIMessage(
            content="你好",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
    )
    llm = LLM("test-key", "http://localhost:1", "fake-model", client=client)
    budget = Budget(deadline_s=10.0, min_remaining_s=0.0)

    result = llm.chat_result(
        [{"role": "user", "content": "hi"}], budget=budget, step="t"
    )

    assert result.text == "你好"
    assert result.model == "fake-model"
    assert budget.calls == 1
    assert budget.prompt_tokens == 10
    assert budget.completion_tokens == 5
    assert budget.total_tokens == 15
    assert budget.cost_cny == pytest.approx(10 / 1e6 * 2.0 + 5 / 1e6 * 8.0)
    assert llm.last_usage.total_tokens == 15


def test_usage_falls_back_to_response_metadata() -> None:
    message = AIMessage(content="ok")
    message.response_metadata["token_usage"] = {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }
    client = _fake_client(message)
    llm = LLM("test-key", "http://localhost:1", "fake", client=client)
    budget = Budget(deadline_s=10.0, min_remaining_s=0.0)

    llm.chat_result([{"role": "user", "content": "x"}], budget=budget)

    assert budget.total_tokens == 10
    assert budget.cost_cny == pytest.approx(7 / 1e6 * 2.0 + 3 / 1e6 * 8.0)


def test_chat_json_extracts_fenced_json() -> None:
    client = _fake_client(AIMessage(content='```json\n{"a": 1}\n```'))
    llm = LLM("test-key", "http://localhost:1", "fake", client=client)

    assert llm.chat_json([{"role": "user", "content": "x"}]) == {"a": 1}


def test_structured_output_parses_schema() -> None:
    structured = MagicMock()
    structured.invoke.return_value = {
        "raw": AIMessage(
            content="...",
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        ),
        "parsed": SelectionOut(tables=["invoices"]),
        "parsing_error": None,
    }
    client = MagicMock()
    client.with_structured_output.return_value = structured
    llm = LLM("test-key", "http://localhost:1", "fake", client=client)
    budget = Budget(deadline_s=10.0, min_remaining_s=0.0)

    data = llm.chat_structured(
        [{"role": "user", "content": "x"}],
        SelectionOut,
        budget=budget,
        step="selector",
    )

    assert data == {"tables": ["invoices"]}
    assert budget.calls == 1
    assert budget.total_tokens == 5
    client.with_structured_output.assert_called_once()


def test_structured_output_parse_failure_raises() -> None:
    structured = MagicMock()
    structured.invoke.return_value = {
        "raw": AIMessage(content="x"),
        "parsed": None,
        "parsing_error": ValueError("bad schema"),
    }
    client = MagicMock()
    client.with_structured_output.return_value = structured
    llm = LLM("test-key", "http://localhost:1", "fake", client=client)

    with pytest.raises(LLMError) as exc_info:
        llm.chat_structured([{"role": "user", "content": "x"}], SelectionOut)

    assert exc_info.value.code == "E4002"


def test_missing_api_key_raises() -> None:
    with pytest.raises(LLMError) as exc_info:
        LLM("", "http://localhost:1", "fake")
    assert exc_info.value.code == "E8001"
    assert exc_info.value.retryable is False


def test_budget_deadline_exhaustion() -> None:
    client = _fake_client(AIMessage(content="x"))
    llm = LLM("test-key", "http://localhost:1", "fake", client=client)
    budget = Budget(deadline_s=0.1, min_remaining_s=0.0)
    time.sleep(0.2)

    with pytest.raises(BudgetExceeded):
        llm.chat_result([{"role": "user", "content": "x"}], budget=budget)


def test_429_retries_once_then_succeeds() -> None:
    err = openai.RateLimitError(
        "429",
        response=httpx.Response(429, request=httpx.Request("POST", "http://x")),
        body=None,
    )
    client = MagicMock()
    client.invoke.side_effect = [err, AIMessage(content="第二次成功")]
    llm = LLM("test-key", "http://localhost:1", "fake", client=client)
    budget = Budget(deadline_s=10.0, min_remaining_s=0.0)

    text = llm.chat([{"role": "user", "content": "x"}], budget=budget, step="t")

    assert text == "第二次成功"
    assert budget.calls == 2


def test_auth_error_is_not_retryable() -> None:
    err = openai.AuthenticationError(
        "401",
        response=httpx.Response(401, request=httpx.Request("POST", "http://x")),
        body=None,
    )
    client = MagicMock()
    client.invoke.side_effect = err
    llm = LLM("test-key", "http://localhost:1", "fake", client=client)
    budget = Budget(deadline_s=10.0, min_remaining_s=0.0)

    with pytest.raises(LLMError) as exc_info:
        llm.chat([{"role": "user", "content": "x"}], budget=budget, step="t")

    assert exc_info.value.code == "E8001"
    assert budget.calls == 1


def test_extract_json_plain() -> None:
    assert extract_json('前缀 {"a": {"b": 2}} 后缀') == {"a": {"b": 2}}
