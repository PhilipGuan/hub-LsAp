"""LangChain 结构化 Agent 的公共封装。"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..config import settings

# 提示词模板：system 为 Agent 角色与规则，human 为 Orchestrator 装配的上下文。
PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [("system", "{system_prompt}"), ("human", "{input}")]
)


def create_chat_model(model: str, temperature: float) -> ChatOpenAI:
    """创建走 OpenAI 兼容端点的聊天模型（DashScope / DeepSeek 均可）。

    API key 只从环境变量 `OPENAI_API_KEY` 读取，不在代码里保存。
    """

    return ChatOpenAI(
        model=model,
        temperature=temperature,
        base_url=settings.base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
    )


@dataclass(frozen=True)
class StructuredAgent:
    """模块级定义：提示词 + 模型 + Pydantic 输出契约。

    模型与链在 `run()` 时才构建，导入模块不会读取 API key，测试也无需真实调用。
    """

    name: str
    model: str
    temperature: float
    output_schema: type[BaseModel]
    system_prompt: str

    def build_chain(self) -> Runnable:
        llm = create_chat_model(self.model, self.temperature)
        # DeepSeek / DashScope 支持 function calling，但不保证 json_schema 严格模式。
        structured = llm.with_structured_output(  # type: ignore[arg-type]
            self.output_schema, method="function_calling"
        )
        return PROMPT_TEMPLATE | structured

    async def run(self, prompt: str) -> BaseModel:
        chain = self.build_chain()
        return await chain.ainvoke(
            {"system_prompt": self.system_prompt, "input": prompt}
        )
