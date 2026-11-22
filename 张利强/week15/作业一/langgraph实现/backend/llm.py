"""共享的 Qwen 客户端工厂（DashScope 的 OpenAI 兼容接口）。"""

from __future__ import annotations

from openai import AsyncOpenAI

from .config import settings


class LLMNotConfigured(RuntimeError):
    """QWEN_API_KEY 缺失或仍是模板占位符。"""


def create_client() -> AsyncOpenAI:
    if not settings.llm_ready:
        raise LLMNotConfigured("QWEN_API_KEY 未配置，请复制 .env.example 为 .env 并填写")

    return AsyncOpenAI(
        api_key=settings.qwen_api_key,
        base_url=settings.qwen_base_url,
        timeout=settings.reply_timeout,
    )
