"""LangGraph 结构化 Agent 的公共封装。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from ..config import settings


class AgentState(TypedDict, total=False):
    """单个 Agent 图的状态：输入提示词，输出结构化结果。"""

    prompt: str
    result: BaseModel | None


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

    每个 Agent 都是一张编译后的 LangGraph 状态图：
    START -> model -> END。模型与图在 `run()` 时才构建，
    导入模块不会读取 API key，测试也无需真实调用。
    """

    name: str
    model: str
    temperature: float
    output_schema: type[BaseModel]
    system_prompt: str

    def build_graph(self):
        llm = create_chat_model(self.model, self.temperature)
        # DeepSeek / DashScope 支持 function calling，但不保证 json_schema 严格模式。
        structured = llm.with_structured_output(  # type: ignore[arg-type]
            self.output_schema, method="function_calling"
        )

        async def call_model(state: AgentState) -> AgentState:
            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=state["prompt"]),
            ]
            return {"result": await structured.ainvoke(messages)}

        graph = StateGraph(AgentState)
        graph.add_node("model", call_model)
        graph.add_edge(START, "model")
        graph.add_edge("model", END)
        return graph.compile()

    async def run(self, prompt: str) -> BaseModel:
        state = await self.build_graph().ainvoke({"prompt": prompt})
        result = state.get("result")
        if result is None:
            raise RuntimeError(f"{self.name} 未返回结构化结果")
        return result
