"""回复生成图：单节点 LangGraph，按情感选择鼓励或排解提示词。

模块自测：python -m backend.langgraph_impl.graphs.reply_graph
"""

from __future__ import annotations

import asyncio
import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from shared.config import get_llm
from shared.schemas import Sentiment


class ReplyState(TypedDict):
    """图状态：text / sentiment 作为输入，reply 由节点写入。"""

    text: str
    sentiment: Sentiment
    reply: str


# 两种情感共用的角色约束：语气、篇幅、免责边界都写死在这里，避免每次调用重复拼
_COMMON = (
    "你是一位温暖、克制的倾听者，用中文回复用户。"
    "先简短共情，再给 2~3 句具体、可执行的建议或鼓励；"
    "不做心理诊断，不承诺疗效，不提及模型或 AI，不使用列表或标题。"
)

# 按情感区分的任务指令，由节点根据 state["sentiment"] 选择
_TASK = {
    "positive": "用户表达了正向情感，请肯定其好状态，鼓励其延续或分享这份感受。",
    "negative": "用户表达了负向情感，请接纳其压力，帮其把情绪说出来，并给一个很小的可执行建议。",
}

# 回复需要一点变化，温度取 0.7；max_tokens 控制单条回复长度
reply_model = get_llm(temperature=0.7, max_tokens=1024)


async def reply_node(state: ReplyState) -> dict:
    """唯一的节点：拼提示词、调用 LLM，把回复写回状态。"""
    result = await reply_model.ainvoke(
        [
            ("system", _COMMON),
            ("human", f"{_TASK[state['sentiment']]}\n\n用户原话：{state['text']}"),
        ]
    )
    # ChatOpenAI 的 content 可能是字符串或内容块列表，统一转成字符串
    reply = str(result.content).strip()
    if not reply:
        raise ValueError("回复生成返回空文本")
    return {"reply": reply}


def build_graph():
    """START -> reply -> END 的单节点链。"""
    builder = StateGraph(ReplyState)
    builder.add_node("reply", reply_node)
    builder.add_edge(START, "reply")
    builder.add_edge("reply", END)
    return builder.compile()


# 与情感图一样，编译一次后复用
graph = build_graph()


async def generate_reply(text: str, sentiment: Sentiment) -> str:
    """worker 调用的入口：输入用户原话和情感标签，返回回复文本。"""
    result = await graph.ainvoke({"text": text, "sentiment": sentiment})
    return result["reply"]


async def main() -> None:
    """模块自测：正向、负向各生成一条回复。"""
    print("正向:", await generate_reply("今天和朋友一起吃饭很开心", "positive"))
    print("负向:", await generate_reply("今天工作好累，感觉什么都做不好", "negative"))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
