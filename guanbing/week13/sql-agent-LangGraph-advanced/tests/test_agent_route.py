"""agent 路线测试：middleware 装配 + `AgentBI` 的人工介入（零网络，脚本假模型）。

`create_agent` 要求真 `BaseChatModel`（`RunnableWithFallbacks` 喂不进去），所以这里用一个
「按脚本吐消息」的假模型顶替：第 1 次请求 `run_sql`，第 2 次给出结论。图的其余部分
（`build_tools` / `HumanInTheLoopMiddleware` / checkpointer）全部保持**真实**，
只有「模型」这一处被替换 —— 于是能在不联网的前提下钉住：

- `build_agent_middleware` 按 `.env` 配置装配出正确 middleware（兜底 / 人工闸门）；
- `human_review=True` 时，`run_sql` 执行前被拦下（`status="interrupted"`），
  `resume({"decisions": [{"type": "approve"}]})` 续接后出结论；
- `human_review=False` 时无闸门，工具直接执行。
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import sqlagent.agents.session as agent_session
import sqlagent.agents.sql_agent as sql_agent_module
from sqlagent.agents.session import AgentBI
from sqlagent.config import LLMSettings

GOOD_SQL = "SELECT COUNT(*) FROM tracks;"
ANSWER = "共有 3503 首歌曲。"


class ScriptedModel(BaseChatModel):
    """脚本假模型：每次调用按序取 `script` 里的一条 AI 消息（用尽后停在最后一条）。"""

    script: list = Field(default_factory=list)
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"

    def bind_tools(self, tools, **kwargs):  # create_agent 要求模型可绑工具
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        msg = self.script[idx].model_copy(deep=True)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def sql_call_then_answer(sql: str = GOOD_SQL, answer: str = ANSWER) -> ScriptedModel:
    """「先请求 run_sql、再给结论」的两步脚本。"""
    return ScriptedModel(script=[
        AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"sql": sql},
                                           "id": "call-1", "type": "tool_call"}]),
        AIMessage(content=answer),
    ])


@pytest.fixture
def no_fallback(monkeypatch):
    """关掉兜底配置，让装配结果不依赖本机 `.env`。"""
    monkeypatch.setattr(sql_agent_module, "get_fallback_settings", lambda *a, **k: None)


@pytest.fixture
def inject_model(monkeypatch):
    """把 `AgentBI` 内部构造的模型换成脚本假模型（middleware / checkpointer 保持真实）。"""
    def install(model: BaseChatModel) -> None:
        real = sql_agent_module.build_autonomous_agent
        monkeypatch.setattr(
            agent_session, "build_autonomous_agent",
            lambda parser, **kw: real(parser, model=model, **kw),
        )
    return install


# --------------------------------------------------------------------------- #
# middleware 装配
# --------------------------------------------------------------------------- #

def test_middleware_empty_when_nothing_configured(no_fallback):
    assert sql_agent_module.build_agent_middleware(human_review=False) == []


def test_middleware_includes_hitl_for_run_sql(no_fallback):
    mw = sql_agent_module.build_agent_middleware(human_review=True)
    assert [type(m).__name__ for m in mw] == ["HumanInTheLoopMiddleware"]
    assert mw[0].interrupt_on["run_sql"]["allowed_decisions"] == ["approve", "edit", "reject"]


def test_middleware_includes_model_fallback_when_configured(monkeypatch):
    backup = LLMSettings(provider="deepseek", model="backup-model",
                         api_key="test-key", base_url="https://example.invalid/v1")
    monkeypatch.setattr(sql_agent_module, "get_fallback_settings", lambda *a, **k: backup)

    mw = sql_agent_module.build_agent_middleware(human_review=False)
    assert [type(m).__name__ for m in mw] == ["ModelFallbackMiddleware"]


# --------------------------------------------------------------------------- #
# AgentBI：HITL 中断 → 续接
# --------------------------------------------------------------------------- #

def test_agent_bi_interrupts_then_approve_executes(parser, no_fallback, inject_model):
    inject_model(sql_call_then_answer())
    bot = AgentBI(parser, human_review=True, thread_id="agent-hitl")

    trace = bot.ask("数据库里一共有多少首歌曲？")
    assert trace["route"] == "agent"
    assert trace["status"] == "interrupted"
    assert trace["raw_question"] == "数据库里一共有多少首歌曲？"
    reqs = trace["interrupt"]["action_requests"]
    assert reqs[0]["name"] == "run_sql"
    assert reqs[0]["args"]["sql"] == GOOD_SQL

    trace = bot.resume({"decisions": [{"type": "approve"}]})
    assert trace["status"] == "done"
    assert trace["sql"] == GOOD_SQL
    assert trace["answer"] == ANSWER


def test_agent_bi_reject_skips_tool_and_finishes(parser, no_fallback, inject_model):
    inject_model(ScriptedModel(script=[
        AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"sql": GOOD_SQL},
                                           "id": "call-1", "type": "tool_call"}]),
        AIMessage(content="已按你的要求取消执行。"),
    ]))
    bot = AgentBI(parser, human_review=True, thread_id="agent-reject")

    assert bot.ask("数据库里一共有多少首歌曲？")["status"] == "interrupted"
    trace = bot.resume({"decisions": [{"type": "reject", "message": "口径不对"}]})

    assert trace["status"] == "done"
    assert trace["answer"] == "已按你的要求取消执行。"


def test_agent_bi_without_review_executes_directly(parser, no_fallback, inject_model):
    inject_model(sql_call_then_answer())
    bot = AgentBI(parser, human_review=False, thread_id="agent-no-review")

    trace = bot.ask("数据库里一共有多少首歌曲？")

    assert trace["status"] == "done"          # 无闸门：一次跑完，不中断
    assert trace["sql"] == GOOD_SQL
    assert trace["answer"] == ANSWER
