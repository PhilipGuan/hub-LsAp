"""LangGraph 实现：把「进程1(情感识别) + 进程2(鼓励/排解)」建模为一张图。

图结构：
    START
      │
      v
  analyze_sentiment (进程1)
      │
      ├── 情感 == positive ──> encourage (进程2：鼓励)
      └── 否则(negative/neutral) ──> comfort (进程2：排解)
      │
      v
     END
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from . import config
from .sentiment import classify_by_llm, classify_rule, template_response


class SentimentState(TypedDict):
    user_input: str
    sentiment: str
    response: str


def _make_client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_BASE_URL,
    )


async def analyze_sentiment(state: SentimentState) -> dict:
    """进程1：情感识别。"""
    text = state["user_input"]
    if config.has_llm():
        sentiment = await classify_by_llm(
            text, client=_make_client(), model=config.OPENAI_MODEL
        )
    else:
        sentiment = classify_rule(text)
    return {"sentiment": sentiment}


def _route(state: SentimentState) -> str:
    """按情感路由到「鼓励」或「排解」节点。"""
    return "encourage" if state["sentiment"] == "positive" else "comfort"


async def encourage(state: SentimentState) -> dict:
    """进程2：正向 -> 鼓励。"""
    return {"response": await _respond(state, mood="鼓励")}


async def comfort(state: SentimentState) -> dict:
    """进程2：负面 -> 排解。"""
    return {"response": await _respond(state, mood="排解")}


async def _respond(state: SentimentState, mood: str) -> str:
    if not config.has_llm():
        return template_response(state["sentiment"])  # type: ignore[arg-type]
    system = (
        "用户情绪正面，请给予真诚的鼓励与肯定，让他/她继续保持好心情。"
        if mood == "鼓励"
        else "用户情绪负面或中性，请共情、排解并温柔地引导他/她积极面对。"
    )
    resp = await _make_client().chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": f"{system} 用中文回答，2-3 句话。"},
            {"role": "user", "content": state["user_input"]},
        ],
        temperature=0.7,
    )
    return (resp.choices[0].message.content or "").strip()


def build_graph():
    graph = StateGraph(SentimentState)
    graph.add_node("analyze_sentiment", analyze_sentiment)
    graph.add_node("encourage", encourage)
    graph.add_node("comfort", comfort)

    graph.add_edge(START, "analyze_sentiment")
    graph.add_conditional_edges(
        "analyze_sentiment",
        _route,
        {"encourage": "encourage", "comfort": "comfort"},
    )
    graph.add_edge("encourage", END)
    graph.add_edge("comfort", END)
    return graph.compile()


# 后端进程调用的统一入口
async def run(user_input: str) -> dict:
    graph = build_graph()
    result = await graph.ainvoke({"user_input": user_input})
    return {
        "sentiment": result["sentiment"],
        "response": result["response"],
        "engine": "langgraph",
    }
