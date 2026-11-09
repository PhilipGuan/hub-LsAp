from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from .budget import Budget, BudgetExceeded, LLMUsage


class LLMError(RuntimeError):
    def __init__(self, message: str, code: str = "E4001", retryable: bool = True):
        self.code = code
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cny: float = 0.0
    model: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_cny": self.cost_cny,
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
        }


def extract_json(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    match = re.search(r"\{.*\}", candidate, re.S)
    if not match:
        raise LLMError(f"模型未返回 JSON: {text[:200]}", "E4002")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON 解析失败: {exc}; 原文: {text[:200]}", "E4002") from exc
    if not isinstance(data, dict):
        raise LLMError(f"模型返回的 JSON 不是对象: {text[:200]}", "E4002")
    return data


class LLM:
    """OpenAI 兼容接口的轻量封装，默认对接 DeepSeek。

    每次调用都从请求级 Budget 扣减；超时上限为 min(剩余预算, 15 s)。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 15.0,
        price_input_cny_per_m: float = 2.0,
        price_output_cny_per_m: float = 8.0,
    ):
        if not api_key:
            raise LLMError(
                "未配置 DEEPSEEK_API_KEY。请在项目根目录创建 .env，"
                "写入 DEEPSEEK_API_KEY=你的key",
                "E8001",
                False,
            )
        self.model = model
        self.default_timeout = max(1.0, float(timeout))
        self.price_input_cny_per_m = price_input_cny_per_m
        self.price_output_cny_per_m = price_output_cny_per_m
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.default_timeout,
            max_retries=0,
        )
        self.last_usage = LLMUsage()

    def _call_once(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        timeout: float,
        budget: Budget,
        step: str,
    ) -> tuple[str, Any]:
        budget.before_call(step)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - 统一映射为稳定错误码
            status = getattr(exc, "status_code", None)
            name = type(exc).__name__.lower()
            if status == 429:
                raise LLMError("模型限流（429）", "E8002") from exc
            if status == 401:
                raise LLMError("API Key 无效或未授权", "E8001", False) from exc
            if status in (400, 403, 404):
                raise LLMError(f"调用大模型失败: {exc}", "E4001", False) from exc
            if status == 408 or "timeout" in name:
                raise LLMError("模型响应超时", "E4004") from exc
            raise LLMError(f"调用大模型失败: {exc}", "E4001") from exc

        usage_obj = getattr(response, "usage", None)
        usage = LLMUsage.from_response(
            usage_obj, self.price_input_cny_per_m, self.price_output_cny_per_m
        )
        budget.record_usage(usage)
        self.last_usage = usage
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise LLMError("模型返回空内容", "E4002")
        return content, usage

    def chat_result(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 2048,
        budget: Budget | None = None,
        step: str = "",
    ) -> LLMResult:
        start = time.perf_counter()
        active_budget = budget or Budget(
            deadline_s=120.0,
            max_llm_calls=10,
            max_total_tokens=10_000_000,
            max_cost_cny=10.0,
            min_remaining_s=0.0,
        )
        last_error: LLMError | None = None
        for attempt in range(2):
            timeout = active_budget.call_timeout(self.default_timeout)
            if active_budget.remaining() <= 0:
                raise BudgetExceeded("E8004", "整请求预算已耗尽")
            try:
                text, usage = self._call_once(
                    messages,
                    temperature,
                    max_tokens,
                    timeout,
                    active_budget,
                    step if attempt == 0 else f"{step or 'llm'}_retry",
                )
                return LLMResult(
                    text=text,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    cost_cny=usage.cost_cny,
                    model=self.model,
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                )
            except LLMError as exc:
                last_error = exc
                retryable = exc.retryable and exc.code in {"E8002", "E4001", "E4004"}
                if not retryable or attempt == 1:
                    raise
                if exc.code == "E8002":
                    if active_budget.remaining() < 3.0 or not active_budget.can_call():
                        raise
                    time.sleep(1.0)
                elif not active_budget.can_call():
                    raise
        raise last_error or LLMError("调用大模型失败", "E4001")

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 2048,
        budget: Budget | None = None,
        step: str = "",
    ) -> str:
        return self.chat_result(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            budget=budget,
            step=step,
        ).text

    def chat_json(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 1024,
        budget: Budget | None = None,
        step: str = "",
    ) -> dict:
        text = self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            budget=budget,
            step=step,
        )
        return extract_json(text)
