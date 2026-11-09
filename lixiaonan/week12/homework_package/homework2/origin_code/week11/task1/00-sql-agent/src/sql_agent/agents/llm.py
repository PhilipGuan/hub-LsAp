"""LangChain 聊天模型工厂：统一接入 OpenAI 兼容端点，API key 只从环境变量读取。"""

from __future__ import annotations

import os

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import BaseOutputParser, PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from ..config import settings


class StructuredJsonParser(BaseOutputParser):
    """从模型文本中提取最外层 JSON 对象并校验为 Pydantic 实例。

    思考模型（deepseek-reasoner 等）不支持通过 tool_choice 强制指定工具，
    不能使用 with_structured_output 的工具调用路径。因此让模型按提示直接
    输出 JSON，再从回复文本中解析：容忍 ```json 围栏与前后缀说明文字。
    """

    pydantic_model: type[BaseModel]

    def parse(self, text: str) -> BaseModel:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise OutputParserException(f"模型输出中未找到 JSON 对象：{text[:200]!r}")
        try:
            return self.pydantic_model.model_validate_json(text[start : end + 1])
        except ValidationError as exc:
            raise OutputParserException(f"模型输出 JSON 校验失败：{exc}") from exc


def chat_model(model: str, temperature: float) -> ChatOpenAI:
    """构建指向兼容端点的聊天模型。

    环境变量缺失时使用占位 key：模型对象可以正常构建（测试可离线运行），
    真正发起请求时才会因 key 无效而失败。
    """

    return ChatOpenAI(
        model=model,
        temperature=temperature,
        base_url=settings.base_url,
        api_key=os.getenv("OPENAI_API_KEY") or "missing-openai-api-key",
    )


def structured_chain(
    prompt: ChatPromptTemplate,
    model: ChatOpenAI,
    output_schema: type[BaseModel],
):
    """在 system 提示中注入 JSON 输出格式约定，并接上容错解析器。

    模板的 system 消息需包含 {format_instructions} 占位符。
    不使用 with_structured_output：它依赖 tool_choice 强制指定工具，
    与思考模式不兼容（400: Thinking mode does not support this tool_choice）。
    """

    instructions = PydanticOutputParser(
        pydantic_object=output_schema
    ).get_format_instructions()
    return prompt.partial(format_instructions=instructions) | (
        model | StructuredJsonParser(pydantic_model=output_schema)
    )
