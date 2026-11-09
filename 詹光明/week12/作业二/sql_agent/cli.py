"""命令行入口：python -m sql_agent "查询问题"。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from .config import settings
from .logging_config import configure_logging
from .workflow import SQLAgentWorkflow


def _read_question(args: list[str]) -> str:
    if args:
        return " ".join(args)
    if sys.stdin.isatty():
        print("请输入查询问题：", file=sys.stderr, end="", flush=True)
        return input()
    return sys.stdin.read().rstrip("\r\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chinook 数据库 SQL Agent（LangChain 版）")
    parser.add_argument("text", nargs="*", help="自然语言查询问题")
    parser.add_argument(
        "--log-level",
        default=os.getenv("SQL_AGENT_LOG_LEVEL", "INFO"),
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认读取 SQL_AGENT_LOG_LEVEL 或 INFO",
    )
    args = parser.parse_args()
    configure_logging(args.log_level)

    if not settings.api_key:
        print(
            "缺少 OPENAI_API_KEY。请先设置环境变量或写入 作业二/.env，例如：\n"
            "  export OPENAI_API_KEY=sk-...\n"
            f"  export OPENAI_BASE_URL={settings.base_url}  # 可选",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not settings.db_path.exists():
        print(
            f"数据库不存在：{settings.db_path}\n"
            "请先运行：python scripts/fetch_chinook.py",
            file=sys.stderr,
        )
        raise SystemExit(1)

    question = _read_question(args.text)
    output = asyncio.run(SQLAgentWorkflow().run(question))
    print(json.dumps(output.model_dump(), ensure_ascii=False, indent=2))
    if output.error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
