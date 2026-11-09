"""CLI 入口：python -m backend "研究主题"。"""
from __future__ import annotations

import argparse
import asyncio
import logging

from .. import config
from ..research import orchestrator

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def _run(topic: str) -> None:
    record = await orchestrator.research(topic)
    out_dir = config.OUTPUT_DIR / record.id
    print("\n===== 研究报告已生成 =====")
    print(f"HTML（浏览器查看）：{out_dir / 'report.html'}")
    print(f"Markdown：{out_dir / 'report.md'}")
    print(f"JSON（数据源）：{out_dir / 'report.json'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="backend", description="深度研究助手：输入主题，产出带来源引用的研究报告"
    )
    parser.add_argument("topic", nargs="+", help="研究主题")
    args = parser.parse_args(argv)
    _setup_logging()
    asyncio.run(_run(" ".join(args.topic)))


if __name__ == "__main__":
    main()
