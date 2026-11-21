"""openai-agents 实现：把「进程1(情感识别) + 进程2(鼓励/排解)」建模为 Agent 编排。

- 进程1：sentiment_agent，用结构化输出(Pydantic)返回情感标签 + 理由。
- 进程2：encourage_agent / comfort_agent，根据情感标签二选一执行。

注：openai-agents 也支持用 handoff() 让 Agent 之间自动交接，这里为了
贴合「进程2 拿到结果后再决定」的需求，采用「先结构化输出、再显式路由」的方式，
并把 handoff 的等价写法留在注释里供参考。
"""
from __future__ import annotations

import functools

from pydantic import BaseModel

from . import config
from .sentiment import classify_rule, template_response


class SentimentResult(BaseModel):
    sentiment: str  # positive / negative / neutral
    reason: str = ""


def _setup_client():
    from openai import AsyncOpenAI

    from agents import set_default_openai_client

    client = AsyncOpenAI(
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_BASE_URL,
    )
    set_default_openai_client(client)
    return client


@functools.lru_cache(maxsize=1)
def _build_agents():
    """惰性构建三个 Agent（进程1 + 进程2 的两条分支）。"""
    from agents import Agent

    _setup_client()

    sentiment_agent = Agent(
        name="sentiment_agent",
        instructions=(
            "你是情感识别助手。判断用户输入的情感倾向，"
            "输出 sentiment：positive（正面）/ negative（负面）/ neutral（中性），"
            "并用一句简短的 reason 说明理由。"
        ),
        output_type=SentimentResult,
        model=config.OPENAI_MODEL,
    )
    encourage_agent = Agent(
        name="encourage_agent",
        instructions="用户情绪正面，请给予真诚的鼓励与肯定，用中文回答 2-3 句话。",
        model=config.OPENAI_MODEL,
    )
    comfort_agent = Agent(
        name="comfort_agent",
        instructions="用户情绪负面或中性，请共情、排解并温柔引导，用中文回答 2-3 句话。",
        model=config.OPENAI_MODEL,
    )
    return sentiment_agent, encourage_agent, comfort_agent


# handoff 等价写法（供参考，实际未启用）：
#   from agents import handoff
#   sentiment_agent = Agent(
#       name="sentiment_agent",
#       instructions="...识别情感后，把对话交给合适的后续 Agent。",
#       handoffs=[handoff(encourage_agent), handoff(comfort_agent)],
#   )


# 后端进程调用的统一入口
async def run(user_input: str) -> dict:
    if not config.has_llm():
        sentiment = classify_rule(user_input)
        return {
            "sentiment": sentiment,
            "response": template_response(sentiment),
            "engine": "openai-agents (rule-fallback)",
        }

    from agents import Runner

    sentiment_agent, encourage_agent, comfort_agent = _build_agents()

    # 进程1：情感识别
    result = await Runner.run(sentiment_agent, user_input)
    parsed: SentimentResult = result.final_output
    sentiment = (parsed.sentiment or "").strip().lower()
    if sentiment not in ("positive", "negative", "neutral"):
        sentiment = classify_rule(user_input)  # LLM 输出异常时兜底

    # 进程2：正向鼓励 / 负面排解
    target = encourage_agent if sentiment == "positive" else comfort_agent
    reply = await Runner.run(target, user_input)

    return {
        "sentiment": sentiment,
        "response": reply.final_output,
        "reason": parsed.reason,
        "engine": "openai-agents",
    }
