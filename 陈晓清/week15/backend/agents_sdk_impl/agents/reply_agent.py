"""回复生成 Agent：正向鼓励 / 负向排解。

模块自测：python -m backend.agents_sdk_impl.agents.reply_agent
"""

from __future__ import annotations

import asyncio
import sys

from agents import Agent, ModelSettings, Runner
from pydantic import BaseModel

from backend.agents_sdk_impl.agents import get_model
from shared.schemas import Sentiment


class ReplyOutput(BaseModel):
    reply: str


_COMMON = (
    "你是一位温暖、克制的倾听者，用中文回复用户。"
    "先简短共情，再给 2~3 句具体、可执行的建议或鼓励；"
    "不做心理诊断，不承诺疗效，不提及模型或 AI，不使用列表或标题。"
)

encourage_agent = Agent(
    name="encourage_agent",
    instructions=_COMMON + "用户表达了正向情感，请肯定其好状态，鼓励其延续或分享这份感受。把回复放在 reply 字段。",
    model=get_model(),
    model_settings=ModelSettings(temperature=0.7),
    output_type=ReplyOutput,
)

comfort_agent = Agent(
    name="comfort_agent",
    instructions=_COMMON + "用户表达了负向情感，请接纳其压力，帮其把情绪说出来，并给一个很小的可执行建议。把回复放在 reply 字段。",
    model=get_model(),
    model_settings=ModelSettings(temperature=0.7),
    output_type=ReplyOutput,
)


async def generate_reply(text: str, sentiment: Sentiment) -> str:
    agent = encourage_agent if sentiment == "positive" else comfort_agent
    result = await Runner.run(agent, input=text)
    if not isinstance(result.final_output, ReplyOutput):
        raise ValueError(f"回复生成返回类型异常: {type(result.final_output).__name__}")
    reply = result.final_output.reply.strip()
    if not reply:
        raise ValueError("回复生成返回空文本")
    return reply


async def main() -> None:
    print("正向:", await generate_reply("今天和朋友一起吃饭很开心", "positive"))
    print("负向:", await generate_reply("今天工作好累，感觉什么都做不好", "negative"))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
