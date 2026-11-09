"""LLM 适配层（S3）：统一 JSON 输出收口——网络重试 + 校验失败回喂重试 + 墙钟硬期限。"""
from __future__ import annotations

import concurrent.futures
import json
import re
import time
from typing import TypeVar

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from . import config

T = TypeVar("T", bound=BaseModel)

_RETRYABLE = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)

# 服务端偶发挂起连接时，SDK 单请求超时可能不生效（真实冒烟踩过：一次调用挂了 9 分钟），
# 这里加一层墙钟硬期限，超时视为可重试错误。
CREATE_DEADLINE_SEC = 180

_client: OpenAI | None = None


class _HardTimeout(RuntimeError):
    """单次请求超过墙钟期限。"""


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not config.DASHSCOPE_API_KEY:
            raise RuntimeError("缺少 DASHSCOPE_API_KEY（请复制 .env.example 为 .env 并填写）")
        _client = OpenAI(api_key=config.DASHSCOPE_API_KEY, base_url=config.LLM_BASE_URL)
    return _client


def _strip_code_fence(text: str) -> str:
    """剥掉 ```json ... ``` 围栏（部分模型偶发输出围栏）。"""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _create_once(model: str, messages: list[dict[str, str]]):
    """单次请求，带墙钟硬期限（到期后抛 _HardTimeout，由上层重试）。"""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(
            get_client().chat.completions.create,
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=60,
        )
        return future.result(timeout=CREATE_DEADLINE_SEC)
    except concurrent.futures.TimeoutError as e:
        raise _HardTimeout(f"单次请求超过 {CREATE_DEADLINE_SEC}s 硬期限") from e
    finally:
        executor.shutdown(wait=False)  # 挂起的线程无法强杀，放任其自行超时退出


def chat_json(
    system: str,
    user: str,
    schema: type[T],
    model: str | None = None,
    network_retries: int = 3,
    feedback_retries: int = 2,
) -> T:
    """调用 LLM 并要求其输出符合 schema 的 JSON。

    - 开启 response_format=json_object（DashScope 兼容模式支持）；
    - 网络类错误（超时/限流/5xx/硬期限）指数退避重试；
    - pydantic 校验失败把错误信息回喂给模型重试，仍失败则抛 RuntimeError。
    """
    model = model or config.LLM_MODEL
    client = get_client()
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    net_tries = 0
    fb_tries = 0
    while True:
        try:
            resp = _create_once(model, messages)
        except (*_RETRYABLE, _HardTimeout) as e:
            net_tries += 1
            if net_tries > network_retries:
                raise RuntimeError(f"LLM 请求失败（已重试 {net_tries - 1} 次）：{e}") from e
            time.sleep(2 ** (net_tries - 1))
            continue

        content = resp.choices[0].message.content or ""
        try:
            return schema.model_validate(json.loads(_strip_code_fence(content)))
        except (json.JSONDecodeError, ValidationError) as e:
            fb_tries += 1
            if fb_tries > feedback_retries:
                raise RuntimeError(f"LLM 输出无法通过 {schema.__name__} 校验：{e}") from e
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"你的输出不合法：{e}\n请只输出修正后的合法 JSON，不要包含解释或代码围栏。"},
            ]
