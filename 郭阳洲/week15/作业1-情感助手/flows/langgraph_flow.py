"""作业1 · 编排层：LangGraph 流水线。

图结构（四类职责 → 六个节点）：
    START → retrieve_memory（记忆检索）
          → classify（情感识别，条件边）
          ├→ cheer（正向鼓励）
          ├→ comfort（负面排解）
          └→ neutral（中性回应）
          → store_memory（记忆回写）→ END

短期/长期记忆均通过 storage 层（SQLite）载入与回写，
与 agents_flow 共用同一存储，保证两框架行为一致。
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from core.config import (
    MAX_KEY_EVENTS,
    MAX_TOPICS,
    MEMORY_WINDOW,
    TITLE_LENGTH,
    get_chat_openai,
)
from core.entities import (
    MEMORY_KEY_EMOTION_TREND,
    MEMORY_KEY_EVENTS,
    MEMORY_KEY_SUMMARY,
    MEMORY_KEY_TOPICS,
    ChatResponse,
    Intensity,
    MemoryUpdateResult,
    Sentiment,
    SentimentResult,
)
from core.prompt_manager import render
from flows.common import (
    format_history,
    format_long_memory,
    merge_unique,
    parse_sentiment_fallback,
)
from storage import repository as repo


# ---------------------------------------------------------------- 状态


class State(TypedDict, total=False):
    session_id: str      # 会话 ID（贯穿全程）
    text: str            # 用户本轮输入
    history: str         # 渲染后的短期记忆文本
    memory: str          # 渲染后的长期记忆文本
    sentiment: str       # 识别结果
    intensity: str       # 情绪强度
    reply: str           # 生成的回应
    summary: str         # 本轮更新的近况摘要


# ---------------------------------------------------------------- 节点 1：记忆检索


def retrieve_memory(state: State) -> dict:
    session_id = state["session_id"]
    msgs = repo.get_recent_messages(session_id, MEMORY_WINDOW)
    memories = repo.get_all_memories(session_id)
    return {
        "history": format_history(msgs),
        "memory": format_long_memory(memories),
    }


# ---------------------------------------------------------------- 节点 2：情感识别（进程1）


def classify(state: State) -> dict:
    prompt = render(
        "classify",
        text=state["text"],
        history=state.get("history"),
    )
    llm = get_chat_openai(temperature=0.0)
    # 注意：DeepSeek 不支持 response_format=json_schema 与强制 tool_choice，
    # 但支持 json_mode（response_format=json_object），提示词中已包含"JSON"字样。
    try:
        parsed = llm.with_structured_output(
            SentimentResult, method="json_mode"
        ).invoke(prompt)
        sentiment, intensity = parsed.sentiment.value, parsed.intensity.value
    except Exception:
        raw = llm.invoke(prompt).content
        sentiment, intensity = parse_sentiment_fallback(str(raw))
    # 流式模式：把识别结果先推给调用方（非流式 invoke 时 writer 为 no-op）
    try:
        writer = get_stream_writer()
    except Exception:
        writer = None
    if writer:
        writer({"type": "sentiment", "sentiment": sentiment, "intensity": intensity})
    return {"sentiment": sentiment, "intensity": intensity}


# ---------------------------------------------------------------- 节点 3：三路回应（进程2）


def _respond(state: State, template: str) -> dict:
    prompt = render(
        template,
        text=state["text"],
        history=state.get("history"),
        memory=state.get("memory"),
    )
    llm = get_chat_openai(temperature=0.7)
    try:
        writer = get_stream_writer()
    except Exception:
        writer = None
    # 逐 token 生成并推送；流结束后拼完整回应
    parts: list[str] = []
    for chunk in llm.stream(prompt):
        text = str(chunk.content)
        if text:
            parts.append(text)
            if writer:
                writer({"type": "delta", "text": text})
    return {"reply": "".join(parts).strip()}


def cheer(state: State) -> dict:
    return _respond(state, "reply_cheer")


def comfort(state: State) -> dict:
    return _respond(state, "reply_comfort")


def neutral(state: State) -> dict:
    return _respond(state, "reply_neutral")


# ---------------------------------------------------------------- 节点 4：记忆回写


def store_memory(state: State) -> dict:
    session_id = state["session_id"]
    text, reply = state["text"], state["reply"]
    sentiment = state["sentiment"]

    # 1) 写入消息（短期记忆）；首条消息设置会话标题
    if repo.count_messages(session_id) == 0:
        repo.update_title(session_id, text[:TITLE_LENGTH])
    repo.add_message(session_id, "user", text, Sentiment(sentiment))
    repo.add_message(session_id, "assistant", reply, Sentiment(sentiment))
    repo.touch_session(session_id)

    # 2) 情绪趋势计数（程序化维护，无需 LLM）
    trend = repo.get_memory_value(session_id, MEMORY_KEY_EMOTION_TREND) or {
        "positive": 0, "negative": 0, "neutral": 0,
    }
    trend[sentiment] = trend.get(sentiment, 0) + 1
    repo.upsert_memory(session_id, MEMORY_KEY_EMOTION_TREND, trend)

    # 3) LLM 提炼关键事件/主题/滚动摘要（长期记忆）
    summary_out: str | None = None
    try:
        existing_events = (
            repo.get_memory_value(session_id, MEMORY_KEY_EVENTS) or {}
        ).get("items", [])
        existing_topics = (
            repo.get_memory_value(session_id, MEMORY_KEY_TOPICS) or {}
        ).get("items", [])
        prompt = render(
            "memory_update",
            history=state.get("history"),
            exchange=f"用户：{text}\n助手：{reply}",
            existing_events="；".join(existing_events),
            existing_topics="；".join(existing_topics),
        )
        upd = get_chat_openai(temperature=0.0).with_structured_output(
            MemoryUpdateResult, method="json_mode"
        ).invoke(prompt)
        repo.upsert_memory(
            session_id, MEMORY_KEY_EVENTS,
            {"items": merge_unique(existing_events, upd.key_events, MAX_KEY_EVENTS)},
        )
        repo.upsert_memory(
            session_id, MEMORY_KEY_TOPICS,
            {"items": merge_unique(existing_topics, upd.topics, MAX_TOPICS)},
        )
        if upd.summary:
            repo.upsert_memory(
                session_id, MEMORY_KEY_SUMMARY, {"text": upd.summary}
            )
            summary_out = upd.summary
    except Exception:
        # 长期记忆失败不阻塞主流程
        pass

    return {"summary": summary_out}


# ---------------------------------------------------------------- 建图


def _route(state: State) -> str:
    """条件边：按识别结果路由到对应回应节点。"""
    return state["sentiment"]


builder = StateGraph(State)
builder.add_node("retrieve_memory", retrieve_memory)
builder.add_node("classify", classify)
builder.add_node("cheer", cheer)
builder.add_node("comfort", comfort)
builder.add_node("neutral", neutral)
builder.add_node("store_memory", store_memory)

builder.add_edge(START, "retrieve_memory")
builder.add_edge("retrieve_memory", "classify")
builder.add_conditional_edges(
    "classify",
    _route,
    {"positive": "cheer", "negative": "comfort", "neutral": "neutral"},
)
for node in ("cheer", "comfort", "neutral"):
    builder.add_edge(node, "store_memory")
builder.add_edge("store_memory", END)

graph = builder.compile()


# ---------------------------------------------------------------- 对外入口（与 agents_flow 同签名）


def run_pipeline(session_id: str | None, text: str) -> ChatResponse:
    """同步执行整条流水线。session_id 为空或不存在时自动新建会话。"""
    if session_id is None or repo.get_session(session_id) is None:
        session_id = repo.create_session().session_id

    result = graph.invoke({"session_id": session_id, "text": text})

    return ChatResponse(
        session_id=session_id,
        framework="langgraph",
        sentiment=Sentiment(result["sentiment"]),
        intensity=Intensity(result["intensity"]),
        reply=result["reply"],
        memory_summary=result.get("summary"),
    )


# ---------------------------------------------------------------- 流式入口


async def run_pipeline_stream(session_id: str | None, text: str):
    """流式执行流水线：依次 yield 事件 dict。

    事件协议：
        {"type": "sentiment", "sentiment", "intensity"}   识别完成
        {"type": "delta", "text"}                          回应文本增量（多次）
        {"type": "done", "session_id", "framework",
         "sentiment", "intensity", "reply", "memory_summary"}  全部完成
    """
    if session_id is None or repo.get_session(session_id) is None:
        session_id = repo.create_session().session_id

    sentiment, intensity = "neutral", "low"
    reply_parts: list[str] = []

    # 图以 custom 流模式执行：节点内 get_stream_writer() 写出的事件在此接收
    async for event in graph.astream(
        {"session_id": session_id, "text": text}, stream_mode="custom"
    ):
        if isinstance(event, dict):
            if event.get("type") == "sentiment":
                sentiment = event.get("sentiment", "neutral")
                intensity = event.get("intensity", "low")
            elif event.get("type") == "delta":
                reply_parts.append(event.get("text", ""))
            yield event

    # store_memory 已把消息/记忆写库，从库中取最终结果组装 done
    msgs = repo.get_recent_messages(session_id)
    reply = msgs[-1].content if msgs else "".join(reply_parts)
    summary = (repo.get_memory_value(session_id, MEMORY_KEY_SUMMARY) or {}).get("text")

    yield {
        "type": "done",
        "session_id": session_id,
        "framework": "langgraph",
        "sentiment": sentiment,
        "intensity": intensity,
        "reply": reply,
        "memory_summary": summary,
    }