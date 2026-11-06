"""LangChain 接入层测试：不联网，只验证链装配与错误提示。"""

import pytest
from langchain_core.runnables import Runnable

from sql_agent.agents.result_summarizer import get_result_summary_chain
from sql_agent.agents.sql_generator import get_sql_generation_chain
from sql_agent.agents.sql_rewriter import get_sql_rewrite_chain
from sql_agent.agents.table_selector import (
    get_table_selection_chain,
    table_selection_prompt,
)
from sql_agent.llm import get_chat_model

CHAIN_GETTERS = (
    get_table_selection_chain,
    get_sql_generation_chain,
    get_sql_rewrite_chain,
    get_result_summary_chain,
)


@pytest.fixture(autouse=True)
def clear_caches():
    get_chat_model.cache_clear()
    for getter in CHAIN_GETTERS:
        getter.cache_clear()
    yield
    get_chat_model.cache_clear()
    for getter in CHAIN_GETTERS:
        getter.cache_clear()


def test_missing_api_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_chat_model("deepseek-flash", 0.0)


def test_all_chains_build_without_network(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for getter in CHAIN_GETTERS:
        assert isinstance(getter(), Runnable)


def test_prompt_treats_question_as_data() -> None:
    messages = table_selection_prompt.format_messages(
        question="总共有多少张专辑？",
        catalog="- albums（347 行）",
        feedback="",
    )
    assert messages[0].type == "system"
    assert "<question>" in messages[-1].content
    assert "albums" in messages[-1].content
