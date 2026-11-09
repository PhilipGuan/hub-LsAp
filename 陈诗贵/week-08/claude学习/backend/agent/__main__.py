"""demo：python -m backend.agent 调一次 LLM 规划（需 DEEPSEEK_API_KEY）。"""
import asyncio
import logging

from . import llm, prompts

logging.basicConfig(level=logging.INFO, format="%(message)s")


async def main() -> None:
    result = await llm.chat_json(prompts.plan("2026 年新能源汽车行业趋势"))
    for sq in result.get("sub_questions", []):
        print(f"- {sq['question']}")


if __name__ == "__main__":
    asyncio.run(main())
