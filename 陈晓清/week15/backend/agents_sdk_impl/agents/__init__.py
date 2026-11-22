"""Agents SDK 公共配置：连接 qwen 的 Chat Completions 兼容模型。"""

from __future__ import annotations

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from openai import AsyncOpenAI

from shared.config import load_settings

# qwen 端点不支持 OpenAI 平台 tracing，关闭以免产生无关请求
set_tracing_disabled(True)

_model: OpenAIChatCompletionsModel | None = None


def get_model() -> OpenAIChatCompletionsModel:
    global _model
    if _model is None:
        settings = load_settings()
        _model = OpenAIChatCompletionsModel(
            model=settings.model,
            openai_client=AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url),
        )
    return _model
