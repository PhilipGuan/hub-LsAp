"""demo：python -m backend.research "主题" 执行研究并落盘（需 DEEPSEEK_API_KEY）。"""
import asyncio
import logging
import sys

from . import orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "天空为什么是蓝色的"
    await orchestrator.research(topic)


if __name__ == "__main__":
    asyncio.run(main())
