"""作业1 · 编排层：OpenAI-Agents 流水线。

Agent 分类（四类职责）：
    sentiment_agent        情感识别（output_type 结构化）
    cheer_agent            正向鼓励
    comfort_agent          负面排解
    neutral_agent          中性回应
    memory_update_agent    记忆回写提炼（output_type 结构化）

编排（后端进程）在 run_pipeline / run_pipeline_stream 中显式串联：
    记忆检索(storage) → Runner.run(sentiment_agent)
    → 按结果选择回应 Agent（Runner.run 或 run_streamed）
    → Runner.run(memory_update_agent) → 记忆回写(storage)

提示词与 LangGraph 完全共用（prompts/*.j2），保证对比公平。
"""
from __future__ import annotations

from agents import Agent, Runner, set_default_openai_client, set_tracing_disabled
from openai.types.responses import ResponseTextDeltaEvent

from core.config import (
    MAX_KEY_EVENTS,
    MAX_TOPICS,
    MEMORY_WINDOW,
    MODEL,
    TITLE_LENGTH,
    get_async_openai_client,
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
    extract_json_dict,
    format_history,
    format_long_memory,
    merge_unique,
    parse_sentiment_fallback,
)
from storage import repository as repo

# 让 openai-agents 走 DeepSeek 的 OpenAI 兼容接口，并关闭 trace 上报
set_default_openai_client(get_async_openai_client(), use_for_tracing=False)
set_tracing_disabled(True)


# ---------------------------------------------------------------- Agent 定义


sentiment_agent = Agent(
    name="情感识别器",
    instructions="你是情感识别器，按提示词要求以 JSON 输出识别结果。",
    model=MODEL,
    output_type=SentimentResult,
)

cheer_agent = Agent(
    name="鼓励回应",
    instructions="你是温暖的陪伴助手，按提示词要求生成鼓励回应。",
    model=MODEL,
)

comfort_agent = Agent(
    name="排解回应",
    instructions="你是温柔的倾听陪伴助手，按提示词要求生成安慰回应。",
    model=MODEL,
)

neutral_agent = Agent(
    name="中性回应",
    instructions="你是自然得体的对话助手，按提示词要求生成平常回应。",
    model=MODEL,
)

memory_update_agent = Agent(
    name="记忆管理器",
    instructions="你是记忆管理器，按提示词要求以 JSON 输出记忆更新结果。",
    model=MODEL,
    output_type=MemoryUpdateResult,
)

_REPLY_AGENTS = {
    Sentiment.POSITIVE: cheer_agent,
    Sentiment.NEGATIVE: comfort_agent,
    Sentiment.NEUTRAL: neutral_agent,
}

_REPLY_TEMPLATES = {
    Sentiment.POSITIVE: "reply_cheer",
    Sentiment.NEGATIVE: "reply_comfort",
    Sentiment.NEUTRAL: "reply_neutral",
}


# ---------------------------------------------------------------- 内部辅助


async def _ensure_session(session_id: str | None) -> str:
    """session_id 为空或不存在时自动新建会话。"""
    if session_id is None or repo.get_session(session_id) is None:
        session_id = repo.create_session().session_id
    return session_id


async def _classify(text: str, history: str) -> tuple[Sentiment, Intensity]:
    """进程1：情感识别（非流式，短输出）。"""
    prompt = render("classify", text=text, history=history)
    result = await Runner.run(sentiment_agent, prompt)
    try:
        parsed = result.final_output_as(SentimentResult)
        return parsed.sentiment, parsed.intensity
    except Exception:
        raw, intensity = parse_sentiment_fallback(str(result.final_output))
        return Sentiment(raw), Intensity(intensity)


async def _persist_turn(
    session_id: str,
    text: str,
    reply: str,
    sentiment: Sentiment,
    history: str,
) -> str | None:
    """回写阶段：消息入库 + 情绪趋势计数 + LLM 提炼长期记忆。

    返回本轮更新的近况摘要（失败返回 None，不阻塞主流程）。
    """
    # 1) 写入消息（短期记忆）；首条消息设置会话标题
    if repo.count_messages(session_id) == 0:
        repo.update_title(session_id, text[:TITLE_LENGTH])
    repo.add_message(session_id, "user", text, sentiment)
    repo.add_message(session_id, "assistant", reply, sentiment)
    repo.touch_session(session_id)

    # 2) 情绪趋势计数（程序化维护）
    trend = repo.get_memory_value(session_id, MEMORY_KEY_EMOTION_TREND) or {
        "positive": 0, "negative": 0, "neutral": 0,
    }
    trend[sentiment.value] = trend.get(sentiment.value, 0) + 1
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
        update_prompt = render(
            "memory_update",
            history=history,
            exchange=f"用户：{text}\n助手：{reply}",
            existing_events="；".join(existing_events),
            existing_topics="；".join(existing_topics),
        )
        result = await Runner.run(memory_update_agent, update_prompt)
        try:
            upd = result.final_output_as(MemoryUpdateResult)
        except Exception:
            # 兜底：模型未走 output_type 工具调用时，从文本中提取 JSON
            data = extract_json_dict(str(result.final_output))
            upd = MemoryUpdateResult.model_validate(data) if data else None
        if upd is not None:
            repo.upsert_memory(
                session_id, MEMORY_KEY_EVENTS,
                {"items": merge_unique(existing_events, upd.key_events, MAX_KEY_EVENTS)},
            )
            repo.upsert_memory(
                session_id, MEMORY_KEY_TOPICS,
                {"items": merge_unique(existing_topics, upd.topics, MAX_TOPICS)},
            )
            if upd.summary:
                repo.upsert_memory(session_id, MEMORY_KEY_SUMMARY, {"text": upd.summary})
                summary_out = upd.summary
    except Exception:
        pass

    return summary_out


# ---------------------------------------------------------------- 非流式入口（与 langgraph_flow 同签名）


async def run_pipeline(session_id: str | None, text: str) -> ChatResponse:
    """异步执行整条流水线。"""
    session_id = await _ensure_session(session_id)

    history = format_history(repo.get_recent_messages(session_id, MEMORY_WINDOW))
    memory = format_long_memory(repo.get_all_memories(session_id))

    # ---- 进程1：情感识别
    sentiment, intensity = await _classify(text, history)

    # ---- 进程2：按情感选择回应 Agent
    reply_prompt = render(
        _REPLY_TEMPLATES[sentiment],
        text=text, history=history, memory=memory,
    )
    result = await Runner.run(_REPLY_AGENTS[sentiment], reply_prompt)
    reply = str(result.final_output).strip()

    # ---- 回写
    summary_out = await _persist_turn(session_id, text, reply, sentiment, history)

    return ChatResponse(
        session_id=session_id,
        framework="agents",
        sentiment=sentiment,
        intensity=intensity,
        reply=reply,
        memory_summary=summary_out,
    )


# ---------------------------------------------------------------- 流式入口（与 langgraph_flow 同协议）


async def run_pipeline_stream(session_id: str | None, text: str):
    """流式执行流水线：依次 yield 事件 dict。

    事件协议：
        {"type": "sentiment", "sentiment", "intensity"}   识别完成
        {"type": "delta", "text"}                          回应文本增量（多次）
        {"type": "done", "session_id", "framework",
         "sentiment", "intensity", "reply", "memory_summary"}  全部完成
    """
    # ---- 0. 会话准备 + 记忆检索
    session_id = await _ensure_session(session_id)
    history = format_history(repo.get_recent_messages(session_id, MEMORY_WINDOW))
    memory = format_long_memory(repo.get_all_memories(session_id))

    # ---- 1. 进程1：情感识别（非流式，短输出）
    sentiment, intensity = await _classify(text, history)
    yield {
        "type": "sentiment",
        "sentiment": sentiment.value,
        "intensity": intensity.value,
    }

    # ---- 2. 进程2：回应 Agent 流式生成（Runner.run_streamed）
    reply_prompt = render(
        _REPLY_TEMPLATES[sentiment],
        text=text, history=history, memory=memory,
    )
    streamed = Runner.run_streamed(_REPLY_AGENTS[sentiment], reply_prompt)
    async for event in streamed.stream_events():
        if (
            event.type == "raw_response_event"
            and isinstance(event.data, ResponseTextDeltaEvent)
            and event.data.delta
        ):
            yield {"type": "delta", "text": event.data.delta}
    reply = str(streamed.final_output).strip()

    # ---- 3. 回写
    summary_out = await _persist_turn(session_id, text, reply, sentiment, history)

    yield {
        "type": "done",
        "session_id": session_id,
        "framework": "agents",
        "sentiment": sentiment.value,
        "intensity": intensity.value,
        "reply": reply,
        "memory_summary": summary_out,
    }