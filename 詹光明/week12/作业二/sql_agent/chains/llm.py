"""ChatOpenAI 客户端的统一构建入口。

LangChain 的 ``ChatOpenAI`` 兼容 OpenAI Chat Completions 协议，因此可以直接
对接 DeepSeek、DashScope 等兼容端点。客户端按 (模型, 温度) 缓存复用。
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from ..config import settings


@lru_cache(maxsize=8)
def get_llm(model: str, temperature: float) -> ChatOpenAI:
    """构建 ChatOpenAI；key 缺失时延迟到请求阶段报错，便于离线导入/测试。"""

    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.api_key or "not-configured",
        base_url=settings.base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
