"""LangChain 模型接入：OpenAI 兼容端点 + Pydantic 结构化输出。

除了环境变量，Web 端还允许每次请求临时覆盖端点地址、API Key 和模型名。
覆盖值放在 ``ContextVar`` 中，只在当前请求的协程上下文里生效，不会污染
其他请求；模型客户端按 (模型名, 温度, 端点, 密钥) 缓存，避免重复建连。

密钥不会写入日志，缓存键里也只使用摘要。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .config import settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@dataclass(frozen=True)
class LLMRuntimeConfig:
    """单次请求的模型配置覆盖；字段为空时回退到环境变量或默认配置。"""

    base_url: str | None = None
    api_key: str | None = None
    primary_model: str | None = None
    summary_model: str | None = None


_EMPTY_CONFIG = LLMRuntimeConfig()
_runtime_config: ContextVar[LLMRuntimeConfig] = ContextVar(
    "sql_agent_llm_runtime", default=_EMPTY_CONFIG
)


def current_llm_config() -> LLMRuntimeConfig:
    """返回当前请求的模型配置覆盖。"""

    return _runtime_config.get()


@contextmanager
def use_llm_config(config: LLMRuntimeConfig) -> Iterator[None]:
    """在当前协程上下文内临时应用模型配置。"""

    token = _runtime_config.set(config)
    try:
        yield
    finally:
        _runtime_config.reset(token)


def llm_cache_key() -> tuple[str, str, str, str]:
    """链缓存的键：端点 + 密钥摘要 + 两个模型名。

    API Key 只保留 SHA-256 摘要，避免明文进入字典键和调试输出。
    """

    config = current_llm_config()
    digest = (
        hashlib.sha256(config.api_key.encode("utf-8")).hexdigest()[:16]
        if config.api_key
        else ""
    )
    return (
        config.base_url or "",
        digest,
        config.primary_model or "",
        config.summary_model or "",
    )


def _require_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "缺少 OPENAI_API_KEY。请先设置环境变量，或在页面右上角的"
            "“接口设置”中填写 API Key。"
        )
    return api_key


@lru_cache(maxsize=32)
def get_chat_model(
    model: str,
    temperature: float,
    base_url: str = "",
    api_key: str = "",
) -> BaseChatModel:
    """构造（并缓存）指向 OpenAI 兼容端点的 ChatOpenAI。"""

    return ChatOpenAI(
        model=model,
        base_url=base_url or settings.base_url,
        api_key=api_key or _require_api_key(),
        temperature=temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def structured_chain(
    prompt: ChatPromptTemplate,
    schema: type[SchemaT],
    *,
    model: str,
    temperature: float,
) -> Runnable[dict[str, Any], SchemaT]:
    """LCEL：提示模板 -> ChatOpenAI -> Pydantic 结构化输出。

    统一使用 function_calling 方式，DashScope / DeepSeek 等兼容端点均已支持；
    json_schema 依赖各服务端的 structured outputs 实现，兼容性更差。
    """

    config = current_llm_config()
    model_runnable = get_chat_model(
        model,
        temperature,
        base_url=config.base_url or "",
        api_key=config.api_key or "",
    ).with_structured_output(schema, method="function_calling")
    return prompt | model_runnable
