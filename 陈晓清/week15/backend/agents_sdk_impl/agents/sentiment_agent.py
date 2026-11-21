"""情感识别 Agent。

模块自测：python -m backend.agents_sdk_impl.agents.sentiment_agent
"""

from __future__ import annotations

import asyncio
import sys

from agents import Agent, ModelSettings, Runner
from pydantic import BaseModel

from backend.agents_sdk_impl.agents import get_model
from shared.schemas import Sentiment


class SentimentResult(BaseModel):
    sentiment: Sentiment


agent = Agent(
    name="sentiment_agent",
    instructions=(
        "你是情感识别助手。判断用户文本的整体情感倾向。"
        "只允许 positive（正向）或 negative（负向），不要输出任何解释。"
    ),
    model=get_model(),
    model_settings=ModelSettings(temperature=0),
    output_type=SentimentResult,
)


async def classify(text: str) -> Sentiment:
    result = await Runner.run(agent, input=text)
    if not isinstance(result.final_output, SentimentResult):
        raise ValueError(f"情感识别返回类型异常: {type(result.final_output).__name__}")
    return result.final_output.sentiment


async def main() -> None:
    print("正向:", await classify("今天和朋友一起吃饭很开心"))
    print("负向:", await classify("今天工作好累，感觉什么都做不好"))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
