"""LLM 工厂：openai-agents 的 Agent(output_type=...) 在这里退化为
"ChatOpenAI + with_structured_output(契约类)"。

API key 只从环境变量读取（OPENAI_API_KEY），代码与配置文件中均不落盘。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from .config import settings
from .models import FinalAnswer, SQLDraft, TableSelection


@dataclass(frozen=True)
class LLMBundle:
    """四个 Agent 对应的结构化输出模型，键名与原 Agent 一一对应。

    字段类型是 Runnable 而非 BaseChatModel：
    with_structured_output 返回的是"模型 + 解析器"复合链，
    invoke(str) 直接得到 Pydantic 实例。
    """

    selection: Runnable  # -> TableSelection
    generation: Runnable  # -> SQLDraft
    rewrite: Runnable  # -> SQLDraft
    summary: Runnable  # -> FinalAnswer


def make_llm(model: str, temperature: float) -> ChatOpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "缺少 OPENAI_API_KEY。请先设置环境变量，例如：\n"
            "  export OPENAI_API_KEY=sk-...\n"
            f"  export OPENAI_BASE_URL={settings.base_url}  # 可选"
        )
    return ChatOpenAI(
        model=model,
        base_url=settings.base_url,
        api_key=api_key,
        temperature=temperature,
    )


def default_llm_bundle() -> LLMBundle:
    """按原实现的模型/温度配置构建四个结构化输出链。

    DeepSeek 兼容端点的两个约束（实测）：
    - response_format=json_schema 不支持（400: This response_format type
      is unavailable now）→ 使用 function_calling 方法（tools 接口）；
    - deepseek-flash 默认思考模式，不支持强制 tool_choice（400: Thinking
      mode does not support this tool_choice）→ 显式传 tool_choice="auto"，
      模型在提示词约束下仍会调用该工具。返回值同样经 Pydantic 校验。
    """

    structured_kwargs = {"method": "function_calling", "tool_choice": "auto"}
    return LLMBundle(
        selection=make_llm(settings.primary_model, 0).with_structured_output(
            TableSelection, **structured_kwargs
        ),
        generation=make_llm(settings.primary_model, 0).with_structured_output(
            SQLDraft, **structured_kwargs
        ),
        rewrite=make_llm(settings.primary_model, 0.2).with_structured_output(
            SQLDraft, **structured_kwargs
        ),
        summary=make_llm(settings.summary_model, 0.3).with_structured_output(
            FinalAnswer, **structured_kwargs
        ),
    )
