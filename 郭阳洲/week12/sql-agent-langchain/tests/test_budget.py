from __future__ import annotations

import pytest

from sql_agent.budget import Budget, BudgetExceeded, LLMUsage


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_budget(clock: FakeClock, **kwargs) -> Budget:
    defaults = dict(
        deadline_s=20.0,
        max_llm_calls=8,
        max_total_tokens=60_000,
        max_cost_cny=0.10,
        min_remaining_s=2.0,
        clock=clock,
    )
    defaults.update(kwargs)
    return Budget(**defaults)


def test_remaining_and_expiry() -> None:
    clock = FakeClock()
    budget = make_budget(clock)
    assert budget.remaining() == pytest.approx(20.0)
    clock.advance(19.0)
    assert budget.remaining() == pytest.approx(1.0)
    assert not budget.can_call()
    with pytest.raises(BudgetExceeded) as exc:
        budget.before_call("generator")
    assert exc.value.code == "E8004"
    clock.advance(2.0)
    assert budget.remaining() == 0.0
    assert budget.expired()


def test_max_llm_calls() -> None:
    clock = FakeClock()
    budget = make_budget(clock, max_llm_calls=2)
    budget.before_call("a")
    budget.before_call("b")
    with pytest.raises(BudgetExceeded) as exc:
        budget.before_call("c")
    assert exc.value.code == "E8003"
    assert budget.call_steps == ["a", "b"]


def test_token_and_cost_limits() -> None:
    clock = FakeClock()
    budget = make_budget(clock, max_total_tokens=100, max_cost_cny=0.01)
    budget.before_call("a")
    budget.record_usage(LLMUsage(60, 60, 120, 0.0002))
    with pytest.raises(BudgetExceeded) as exc:
        budget.before_call("b")
    assert exc.value.code == "E8003"

    clock = FakeClock()
    budget = make_budget(clock, max_cost_cny=0.0001)
    budget.before_call("a")
    budget.record_usage(LLMUsage(1, 1, 2, 0.0002))
    with pytest.raises(BudgetExceeded):
        budget.before_call("b")


def test_cancel_and_timeout_cap() -> None:
    clock = FakeClock()
    budget = make_budget(clock, deadline_s=10.0)
    assert budget.call_timeout(15.0) == pytest.approx(10.0)
    clock.advance(8.0)
    assert budget.call_timeout(15.0) == pytest.approx(2.0)
    budget.cancel()
    assert budget.cancelled
    assert budget.expired()
    with pytest.raises(BudgetExceeded) as exc:
        budget.before_call()
    assert exc.value.code == "E8004"


def test_usage_cost() -> None:
    usage = LLMUsage.from_response(
        type("U", (), {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}),
        2.0,
        8.0,
    )
    assert usage.total_tokens == 2_000_000
    assert usage.cost_cny == pytest.approx(10.0)


def test_ensure_time() -> None:
    clock = FakeClock()
    budget = make_budget(clock)
    budget.ensure_time(5.0)
    clock.advance(16.0)
    with pytest.raises(BudgetExceeded):
        budget.ensure_time(5.0)
