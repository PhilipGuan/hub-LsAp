"""Qwen 模型接入。

通过 OpenAI 兼容协议调用阿里云百炼（DashScope），复用同一个 AsyncOpenAI 客户端。
"""

from __future__ import annotations

from functools import lru_cache

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from openai import AsyncOpenAI

from backend.config import (
    AGENTS_TRACING_DISABLED,
    DASHSCOPE_API_KEY,
    LLM_TIMEOUT_SECONDS,
    QWEN_BASE_URL,
    QWEN_MODEL,
)

# 使用 Qwen 时没有 OpenAI 平台 key，trace() 无法上传，自动关闭导出（仍可本地传参）。
set_tracing_disabled(AGENTS_TRACING_DISABLED)


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    """全局复用连接池；不要把空 key 藏起来，启动时会由 require_api_key() 报错。"""
    return AsyncOpenAI(
        api_key=DASHSCOPE_API_KEY or "missing-dashscope-api-key",
        base_url=QWEN_BASE_URL,
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=0,  # 重试由各 service 自己控制，避免重试次数叠加
    )


@lru_cache(maxsize=1)
def get_model() -> OpenAIChatCompletionsModel:
    return OpenAIChatCompletionsModel(model=QWEN_MODEL, openai_client=get_client())
