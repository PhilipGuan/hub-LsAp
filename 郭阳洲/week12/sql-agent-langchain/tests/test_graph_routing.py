"""LangGraph 编排的离线路由测试：预算耗尽、改写空转等原流程未覆盖的分支。"""

from __future__ import annotations

from dataclasses import replace

from sql_agent.agent import SQLAgent
from sql_agent.config import settings as default_settings
from sql_agent.llm import extract_json
from sql_agent.trace_store import NullTraceStore

SELECTOR_INVOICES = '{"tables": ["invoices"], "reason": "发票统计", "confidence": "high"}'


class FakeLLM:
    """支持同一 step 多次调用返回不同响应：值取 list 时按序弹出。"""

    model = "fake-model"

    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.calls: list[str] = []

    def _reply(self, step: str) -> str:
        self.calls.append(step)
        if step not in self.responses:
            raise AssertionError(f"未配置 step={step} 的响应")
        value = self.responses[step]
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"step={step} 的响应序列已耗尽")
            text = value.pop(0)
        else:
            text = value
        return str(text)

    def chat(self, messages, temperature=0.0, max_tokens=2048, budget=None, step=""):
        return self._reply(step)

    def chat_json(self, messages, temperature=0.0, max_tokens=1024, budget=None, step=""):
        return extract_json(self._reply(step))

    def chat_structured(self, messages, schema, temperature=0.0, max_tokens=1024, budget=None, step=""):
        return self.chat_json(messages, budget=budget, step=step)


class BudgetAwareFakeLLM(FakeLLM):
    """走真实 Budget 记账的 Fake，用于触发预算耗尽分支。"""

    def _reply(self, step: str, budget=None) -> str:
        self.calls.append(step)
        if budget is not None:
            budget.before_call(step)  # 真实预算检查，耗尽时抛 BudgetExceeded
        if step not in self.responses:
            raise AssertionError(f"未配置 step={step} 的响应")
        return self.responses[step]

    def chat(self, messages, temperature=0.0, max_tokens=2048, budget=None, step=""):
        return self._reply(step, budget)

    def chat_json(self, messages, temperature=0.0, max_tokens=1024, budget=None, step=""):
        return extract_json(self._reply(step, budget))

    def chat_structured(self, messages, schema, temperature=0.0, max_tokens=1024, budget=None, step=""):
        return self.chat_json(messages, budget=budget, step=step)


def make_agent(fake, tmp_path, **overrides) -> SQLAgent:
    settings = replace(
        default_settings,
        trace_dir=tmp_path / "traces",
        feedback_db=tmp_path / "feedback.db",
        **overrides,
    )
    return SQLAgent(llm=fake, settings=settings, trace_store=NullTraceStore())


def test_budget_exhaustion_mid_flow_returns_e8003(tmp_path) -> None:
    """LLM 调用次数预算耗尽：选表成功后生成阶段被拒，错误码 E8003。"""
    fake = BudgetAwareFakeLLM({"selector": SELECTOR_INVOICES})
    agent = make_agent(fake, tmp_path, max_llm_calls=1)

    result = agent.run("有多少张发票？")

    assert result.status == "error"
    assert result.error_code == "E8003"
    assert result.success is False
    # fake 在 before_call 抛出前已记录 generator 尝试；真实预算只记了 selector 1 次
    assert fake.calls == ["selector", "generator"]
    assert result.budget["calls"] == 1


def test_rewrite_noop_stops_with_e6010(tmp_path) -> None:
    """改写返回与原 SQL 相同的查询：立即终止，不继续浪费预算。"""
    bad_sql = "```sql\nSELECT COUNT(*) AS n FROM invoicez\n```"
    fake = FakeLLM(
        {"selector": SELECTOR_INVOICES, "generator": bad_sql, "rewriter": bad_sql}
    )
    agent = make_agent(fake, tmp_path)

    result = agent.run("有多少张发票？")

    assert result.status == "error"
    assert result.error_code == "E6010"
    assert result.attempts == 1
    assert fake.calls == ["selector", "generator", "rewriter"]


def test_rewrite_loop_respects_max_rewrites(tmp_path) -> None:
    """连续改写仍失败：attempts 达到 max_rewrites+1 后停止，改写仅发生 2 次。"""
    bad_sql = "```sql\nSELECT COUNT(*) AS n FROM invoicez\n```"
    fake = FakeLLM(
        {
            "selector": SELECTOR_INVOICES,
            "generator": bad_sql,
            "rewriter": [
                "```sql\nSELECT COUNT(*) AS m FROM invoicezz\n```",
                "```sql\nSELECT COUNT(*) AS k FROM invoicezzz\n```",
            ],
        }
    )
    agent = make_agent(fake, tmp_path, max_rewrites=2)

    result = agent.run("有多少张发票？")

    assert result.status == "error"
    assert result.attempts == 3  # 1 次初始校验 + 2 次改写后校验
    assert fake.calls.count("rewriter") == 2
    assert result.error_code != "E6010"  # 每次都产生了新 SQL，最终因上限终止
