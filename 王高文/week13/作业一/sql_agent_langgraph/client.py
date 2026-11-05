"""LangChain 模型与结构化输出链工厂。"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .config import settings


def create_structured_chain(
    *,
    model_name: str,
    temperature: float,
    output_schema: type[BaseModel],
    system_prompt: str,
    human_prompt: str,
) -> Any:
    """创建结构化输出链；API key 只从环境变量读取。"""

    model_options: dict[str, Any] = {
        "model": model_name,
        "temperature": temperature,
        "base_url": settings.base_url,
    }
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        model_options["api_key"] = api_key

    model = ChatOpenAI(**model_options)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", human_prompt),
        ]
    )
    return prompt | model.with_structured_output(output_schema)
