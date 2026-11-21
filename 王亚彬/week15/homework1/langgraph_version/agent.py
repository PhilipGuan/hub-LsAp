"""LangGraph 版：进程1 情感识别 → 进程2 情绪回应"""
import os
from typing import TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

llm = ChatOpenAI(
    model=os.getenv("MODEL", "gpt-4o-mini"),
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL") or None,
    temperature=0.7,
)

LABELS = ("正向", "负面", "中性")


class State(TypedDict):
    user_input: str
    sentiment: str
    reply: str


def analyze_sentiment(state: State) -> dict:
    """进程1：情感识别"""
    msg = llm.invoke([
        ("system", "你是情感分析器。判断用户输入的总体情感倾向，"
                   "只输出一个词：正向 / 负面 / 中性，不要输出任何解释。"),
        ("user", state["user_input"]),
    ])
    text = str(msg.content)
    label = next((k for k in LABELS if k in text), "中性")
    return {"sentiment": label}


def generate_reply(state: State) -> dict:
    """进程2：正向则鼓励，负面则排解"""
    label = state["sentiment"]
    if label == "正向":
        role = "用户情绪积极。请真诚地鼓励和肯定他，回应他说的具体内容，避免空泛套话。"
    elif label == "负面":
        role = "用户情绪消极。请先共情安抚，再帮他拆解情绪，给出 1-2 条可操作的建议。"
    else:
        role = "用户情绪中性。请友好自然地回应，并顺着话题继续聊下去。"

    msg = llm.invoke([
        ("system", role + " 回复控制在 150 字以内，语气亲切，"
                         "不要提及'情感识别'这类内部术语。"),
        ("user", state["user_input"]),
    ])
    return {"reply": str(msg.content)}


def build_graph():
    g = StateGraph(State)
    g.add_node("sentiment", analyze_sentiment)   # 进程1
    g.add_node("respond", generate_reply)        # 进程2
    g.set_entry_point("sentiment")
    g.add_edge("sentiment", "respond")
    g.add_edge("respond", END)
    return g.compile()


graph = build_graph()
