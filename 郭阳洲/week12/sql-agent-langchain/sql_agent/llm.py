from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Type

from pydantic import BaseModel

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


def _map_error(exc: Exception) -> LLMError:
    """把底层异常映射为稳定错误码（与原 OpenAI 封装保持一致）。"""
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.lower()
    if status == 429:
        return LLMError("模型限流（429）", "E8002")
    if status == 401:
        return LLMError("API Key 无效或未授权", "E8001", False)
    if status in (400, 403, 404):
        return LLMError(f"调用大模型失败: {exc}", "E4001", False)
    if status == 408 or "timeout" in name:
        return LLMError("模型响应超时", "E4004")
    return LLMError(f"调用大模型失败: {exc}", "E4001")


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            else:
                parts.append(str(getattr(part, "text", "") or ""))
        content = "".join(parts)
    return str(content or "").strip()


class LLM:
    """LangChain ChatOpenAI 封装（默认对接 DeepSeek，OpenAI 兼容接口）。

    - 门面方法 chat / chat_json / chat_structured / chat_result 与旧版签名兼容，
      selector/generator/rewriter/summarizer 无需感知底层变化；
    - 每次调用从请求级 Budget 扣减；per-call 超时 = min(剩余预算, 默认上限)；
    - `max_retries=0`，429/超时等按原语义在门面内重试一次；
    - `chat_structured` 走 `with_structured_output`（Pydantic schema），
      解析失败抛 LLMError(E4002)，由上层回退到普通 JSON 或模板答案。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 15.0,
        price_input_cny_per_m: float = 2.0,
        price_output_cny_per_m: float = 8.0,
        *,
        client: Any | None = None,
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
        self._api_key = api_key
        self._base_url = base_url
        self._client = client  # 测试注入用；生产为 ChatOpenAI 按参数缓存
        self._clients: dict[tuple[float, float, int], Any] = {}
        self.last_usage = LLMUsage()

    # ---------- client ----------

    def _make_client(self, timeout: float, temperature: float, max_tokens: int):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=self.model,
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=timeout,
            max_retries=0,
            temperature=temperature,
            max_tokens=max_tokens or None,
        )

    def _client_for(
        self, timeout: float, temperature: float, max_tokens: int
    ) -> Any:
        """按 (超时, 温度, max_tokens) 缓存 ChatOpenAI 实例，实现 per-call 超时。"""
        if self._client is not None:
            return self._client
        key = (round(max(0.5, float(timeout)), 1), float(temperature), int(max_tokens or 0))
        client = self._clients.get(key)
        if client is None:
            client = self._make_client(key[0], temperature, max_tokens)
            self._clients[key] = client
        return client

    # ---------- 单次调用 ----------

    def _chat_once(
        self,
        messages: list,
        temperature: float,
        max_tokens: int,
        timeout: float,
        budget: Budget,
        step: str,
    ) -> tuple[str, LLMUsage]:
        budget.before_call(step)
        try:
            response = self._client_for(
                timeout, temperature, max_tokens
            ).invoke(list(messages))
        except Exception as exc:  # noqa: BLE001 - 统一映射为稳定错误码
            raise _map_error(exc) from exc
        usage = self._usage_with_prices(response)
        budget.record_usage(usage)
        self.last_usage = usage
        text = _message_text(response)
        if not text:
            raise LLMError("模型返回空内容", "E4002")
        return text, usage

    def _structured_once(
        self,
        messages: list,
        schema: Type[BaseModel],
        temperature: float,
        max_tokens: int,
        timeout: float,
        budget: Budget,
        step: str,
    ) -> dict:
        budget.before_call(step)
        try:
            client = self._client_for(timeout, temperature, max_tokens)
            out = client.with_structured_output(schema, include_raw=True).invoke(
                list(messages)
            )
        except Exception as exc:  # noqa: BLE001 - 统一映射为稳定错误码
            raise _map_error(exc) from exc
        if not isinstance(out, dict):
            raise LLMError("结构化输出返回格式异常", "E4002")
        raw = out.get("raw")
        parsed = out.get("parsed")
        parsing_error = out.get("parsing_error")
        usage = self._usage_with_prices(raw) if raw is not None else LLMUsage()
        budget.record_usage(usage)
        self.last_usage = usage
        if parsed is None:
            raise LLMError(f"结构化输出解析失败: {parsing_error}", "E4002")
        if isinstance(parsed, BaseModel):
            return parsed.model_dump()
        return dict(parsed)

    def _usage_with_prices(self, message: Any) -> LLMUsage:
        meta = getattr(message, "usage_metadata", None) or {}
        prompt = int(meta.get("input_tokens", 0) or 0)
        completion = int(meta.get("output_tokens", 0) or 0)
        total = int(meta.get("total_tokens", 0) or (prompt + completion))
        if not total:
            token_usage = (getattr(message, "response_metadata", None) or {}).get(
                "token_usage"
            ) or {}
            prompt = int(token_usage.get("prompt_tokens", 0) or 0)
            completion = int(token_usage.get("completion_tokens", 0) or 0)
            total = int(token_usage.get("total_tokens", 0) or (prompt + completion))
        cost = (
            prompt / 1_000_000 * self.price_input_cny_per_m
            + completion / 1_000_000 * self.price_output_cny_per_m
        )
        return LLMUsage(prompt, completion, total, round(cost, 8))

    # ---------- 重试 ----------

    def _should_retry(self, exc: LLMError, failures: int, budget: Budget) -> bool:
        if failures >= 2:
            return False
        if not (exc.retryable and exc.code in {"E8002", "E4001", "E4004"}):
            return False
        if exc.code == "E8002":
            return budget.remaining() >= 3.0 and budget.can_call()
        return budget.can_call()

    # ---------- 门面 ----------

    def chat_result(
        self,
        messages: list,
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
        failures = 0
        while True:
            timeout = active_budget.call_timeout(self.default_timeout)
            if active_budget.remaining() <= 0:
                raise BudgetExceeded("E8004", "整请求预算已耗尽")
            try:
                text, usage = self._chat_once(
                    messages,
                    temperature,
                    max_tokens,
                    timeout,
                    active_budget,
                    step if failures == 0 else f"{step or 'llm'}_retry",
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
                failures += 1
                if not self._should_retry(exc, failures, active_budget):
                    raise
                if exc.code == "E8002":
                    time.sleep(1.0)

    def chat(
        self,
        messages: list,
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
        messages: list,
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

    def chat_structured(
        self,
        messages: list,
        schema: Type[BaseModel],
        temperature: float = 0.0,
        max_tokens: int = 1024,
        budget: Budget | None = None,
        step: str = "",
    ) -> dict:
        """LangChain with_structured_output：返回 schema 字段的 dict。"""
        active_budget = budget or Budget(
            deadline_s=120.0,
            max_llm_calls=10,
            max_total_tokens=10_000_000,
            max_cost_cny=10.0,
            min_remaining_s=0.0,
        )
        failures = 0
        while True:
            timeout = active_budget.call_timeout(self.default_timeout)
            if active_budget.remaining() <= 0:
                raise BudgetExceeded("E8004", "整请求预算已耗尽")
            try:
                return self._structured_once(
                    messages,
                    schema,
                    temperature,
                    max_tokens,
                    timeout,
                    active_budget,
                    step if failures == 0 else f"{step or 'llm'}_retry",
                )
            except LLMError as exc:
                failures += 1
                if not self._should_retry(exc, failures, active_budget):
                    raise
                if exc.code == "E8002":
                    time.sleep(1.0)
