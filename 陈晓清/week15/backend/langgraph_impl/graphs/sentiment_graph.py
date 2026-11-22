"""情感识别图：单节点 LangGraph + ChatOpenAI 结构化输出。

模块自测：python -m backend.langgraph_impl.graphs.sentiment_graph
"""

from __future__ import annotations

import asyncio
import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from shared.config import get_llm
from shared.schemas import Sentiment


class SentimentResult(BaseModel):
    """LLM 结构化输出的载体：把模型输出限制为 positive / negative，而不是自由文本。"""

    sentiment: Sentiment


class SentimentState(TypedDict):
    """图状态：节点读 state["text"]，返回的 dict 会合并回状态并补上 sentiment。"""

    text: str
    sentiment: Sentiment


# temperature=0 让分类结果稳定；with_structured_output 把 Pydantic 模型转成 JSON Schema 约束模型输出
classifier = get_llm(temperature=0).with_structured_output(SentimentResult)


async def classify_node(state: SentimentState) -> dict:
    """唯一的节点：调用 LLM 判定情感，只返回需要更新的字段。"""
    result = await classifier.ainvoke(
        [
            ("system", "你是情感识别助手。判断用户文本的整体情感倾向，只允许 positive 或 negative。"),
            ("human", state["text"]),
        ]
    )
    # 结构化输出理论上已保证类型，这里再兜一次底，避免非法值流进 Kafka
    if not isinstance(result, SentimentResult):
        raise ValueError(f"情感识别返回类型异常: {type(result).__name__}")
    return {"sentiment": result.sentiment}


def build_graph():
    """START -> classify -> END 的单节点链；以后要加节点就在这里加节点和边。"""
    builder = StateGraph(SentimentState)
    builder.add_node("classify", classify_node)
    builder.add_edge(START, "classify")
    builder.add_edge("classify", END)
    return builder.compile()


# 图编译一次、进程内复用，不必每条消息都重新建图
graph = build_graph()


async def classify_sentiment(text: str) -> Sentiment:
    """worker 调用的入口：输入原始文本，返回 positive / negative。"""
    # ainvoke 的返回值是执行结束后的完整状态，节点更新过的字段都在里面
    result = await graph.ainvoke({"text": text})
    return result["sentiment"]


async def main() -> None:
    """模块自测：各跑一条正向、负向文本。"""
    print("正向:", await classify_sentiment("今天和朋友一起吃饭很开心"))
    print("负向:", await classify_sentiment("今天工作好累，感觉什么都做不好"))


if __name__ == "__main__":
    # Windows 控制台默认编码可能不是 UTF-8，中文会乱码
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
