"""demo：python -m backend.tools 执行一次搜索。"""
import asyncio
import logging

from . import search

logging.basicConfig(level=logging.INFO, format="%(message)s")


async def main() -> None:
    sources = await search.search("天空为什么是蓝色的", count=3)
    for i, s in enumerate(sources):
        print(f"[{i}] {s.title} — {s.url}（{s.site}）")


if __name__ == "__main__":
    asyncio.run(main())
