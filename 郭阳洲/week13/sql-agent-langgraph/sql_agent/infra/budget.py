from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


class BudgetExceeded(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cny: float = 0.0

    @classmethod
    def from_response(
        cls,
        usage: object | None,
        price_input_cny_per_m: float,
        price_output_cny_per_m: float,
    ) -> "LLMUsage":
        if usage is None:
            return cls()
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        total = int(getattr(usage, "total_tokens", 0) or (prompt + completion))
        cost = (
            prompt / 1_000_000 * price_input_cny_per_m
            + completion / 1_000_000 * price_output_cny_per_m
        )
        return cls(prompt, completion, total, round(cost, 8))


@dataclass
class BudgetSnapshot:
    deadline_s: float
    elapsed_s: float
    remaining_s: float
    calls: int
    max_llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    max_total_tokens: int
    cost_cny: float
    max_cost_cny: float
    cancelled: bool
    call_steps: list[str] = field(default_factory=list)


class Budget:
    """一次请求的共享预算：LLM 调用、重试、改写、SQL 执行都从这里扣。"""

    def __init__(
        self,
        deadline_s: float = 20.0,
        max_llm_calls: int = 8,
        max_total_tokens: int = 60_000,
        max_cost_cny: float = 0.10,
        min_remaining_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.deadline_s = max(0.1, float(deadline_s))
        self.max_llm_calls = max(1, int(max_llm_calls))
        self.max_total_tokens = max(1, int(max_total_tokens))
        self.max_cost_cny = max(0.0, float(max_cost_cny))
        self.min_remaining_s = max(0.0, float(min_remaining_s))
        self._clock = clock
        self._started = clock()
        self._cancelled = False
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cost_cny = 0.0
        self.call_steps: list[str] = []

    @property
    def started(self) -> float:
        return self._started

    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started)

    def remaining(self) -> float:
        return max(0.0, self.deadline_s - self.elapsed())

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def expired(self) -> bool:
        return self._cancelled or self.remaining() <= 0

    def can_call(self) -> bool:
        return (
            not self.expired()
            and self.remaining() >= self.min_remaining_s
            and self.calls < self.max_llm_calls
            and self.total_tokens < self.max_total_tokens
            and self.cost_cny < self.max_cost_cny
        )

    def before_call(self, step: str = "") -> None:
        if self._cancelled:
            raise BudgetExceeded("E8004", "查询已取消")
        if self.remaining() <= 0:
            raise BudgetExceeded("E8004", "整请求 20 秒预算已耗尽")
        if self.remaining() < self.min_remaining_s:
            raise BudgetExceeded(
                "E8004", f"剩余预算不足 {self.min_remaining_s:.0f} 秒，不再发起调用"
            )
        if self.calls >= self.max_llm_calls:
            raise BudgetExceeded(
                "E8003", f"LLM 调用次数已达上限 {self.max_llm_calls}"
            )
        if self.total_tokens >= self.max_total_tokens:
            raise BudgetExceeded("E8003", "Token 预算已耗尽")
        if self.cost_cny >= self.max_cost_cny:
            raise BudgetExceeded("E8003", "成本预算已耗尽")
        self.calls += 1
        self.call_steps.append(step or f"call_{self.calls}")

    def record_usage(self, usage: LLMUsage) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.total_tokens += usage.total_tokens
        self.cost_cny = round(self.cost_cny + usage.cost_cny, 8)

    def call_timeout(self, cap_s: float = 15.0) -> float:
        return max(0.5, min(float(cap_s), self.remaining()))

    def ensure_time(self, need_s: float = 0.0) -> None:
        if self._cancelled:
            raise BudgetExceeded("E8004", "查询已取消")
        if self.remaining() <= need_s:
            raise BudgetExceeded(
                "E8004", f"剩余预算不足，无法继续（需要约 {need_s:.0f} 秒）"
            )

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            deadline_s=self.deadline_s,
            elapsed_s=round(self.elapsed(), 3),
            remaining_s=round(self.remaining(), 3),
            calls=self.calls,
            max_llm_calls=self.max_llm_calls,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            max_total_tokens=self.max_total_tokens,
            cost_cny=round(self.cost_cny, 6),
            max_cost_cny=self.max_cost_cny,
            cancelled=self._cancelled,
            call_steps=list(self.call_steps),
        )
