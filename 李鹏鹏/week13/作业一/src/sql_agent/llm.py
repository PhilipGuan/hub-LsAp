"""LangChain 模型接入：OpenAI 兼容端点 + Pydantic 结构化输出。

模型客户端按 (模型名, 温度) 缓存，避免每次请求重建；构造延迟到首次调用链时进行，
这样导入包和运行不调用 LLM 的单元测试都不要求 API key。
"""

from __future__ import annotations

import os
from functools import cache
from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .config import settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _require_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "缺少 OPENAI_API_KEY。请先设置环境变量，例如：\n"
            "  export OPENAI_API_KEY=sk-...\n"
            f"  export OPENAI_BASE_URL={settings.base_url}  # 可选"
        )
    return api_key


@cache
def get_chat_model(model: str, temperature: float) -> BaseChatModel:
    """构造（并缓存）指向 OpenAI 兼容端点的 ChatOpenAI。"""

    return ChatOpenAI(
        model=model,
        base_url=settings.base_url,
        api_key=_require_api_key(),
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

    model_runnable = get_chat_model(model, temperature).with_structured_output(
        schema, method="function_calling"
    )
    return prompt | model_runnable
