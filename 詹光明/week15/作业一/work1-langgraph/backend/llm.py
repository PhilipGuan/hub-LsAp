"""ChatOpenAI 客户端的统一构建入口。

DeepSeek、DashScope 等兼容 OpenAI Chat Completions 协议的服务都可以直接复用，
只需要改 ``.env`` 里的 ``OPENAI_BASE_URL`` / ``OPENAI_MODEL``。
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .config import get_settings


@lru_cache(maxsize=8)
def get_llm(model: str, temperature: float) -> ChatOpenAI:
    """按 (模型, 温度) 缓存客户端；key 缺失时延迟到请求阶段报错。"""

    settings = get_settings()
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.api_key or "not-configured",
        base_url=settings.base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def structured_llm(schema: type[BaseModel], temperature: float):
    """返回带结构化输出能力的 llm，调用后直接得到 ``schema`` 实例或 dict。"""

    settings = get_settings()
    return get_llm(settings.model, temperature).with_structured_output(schema)
