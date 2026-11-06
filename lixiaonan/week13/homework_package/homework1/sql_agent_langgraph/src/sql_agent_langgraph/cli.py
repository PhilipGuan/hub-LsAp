"""命令行入口：python -m sql_agent_langgraph "查询问题"。

日志到 stderr，最终 JSON 到 stdout，便于管道处理。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .config import settings
from .logging_config import configure_logging


def _read_question(args: list[str]) -> str:
    if args:
        return " ".join(args)
    if sys.stdin.isatty():
        print("请输入查询问题：", file=sys.stderr, end="", flush=True)
        return input()
    return sys.stdin.read().rstrip("\r\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chinook 数据库 SQL Agent（LangGraph）")
    parser.add_argument("text", nargs="*", help="自然语言查询问题")
    parser.add_argument(
        "--log-level",
        default=os.getenv("SQL_AGENT_LOG_LEVEL", "INFO"),
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认读取 SQL_AGENT_LOG_LEVEL 或 INFO",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="逐节点流式输出每个节点的状态更新（stderr）",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="checkpoint 线程 ID；同一线程可通过 get_state_history 回放",
    )
    args = parser.parse_args()
    configure_logging(args.log_level)

    question = _read_question(args.text)
    if not question.strip():
        print(
            json.dumps(
                {"question": "", "answer": "请输入要查询的问题。"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not os.getenv("OPENAI_API_KEY"):
        print(
            "缺少 OPENAI_API_KEY。请先设置环境变量，例如：\n"
            "  export OPENAI_API_KEY=sk-...\n"
            f"  export OPENAI_BASE_URL={settings.base_url}  # 可选",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # 延迟导入：让缺 key / 空问题的报错不依赖 catalog 解析。
    from .graph import SQLAgentGraph

    workflow = SQLAgentGraph()

    if args.stream:

        def _print_update(node: str, update: dict) -> None:
            print(f"=== node: {node} ===", file=sys.stderr)
            for key, value in update.items():
                text = str(value)
                if len(text) > 300:
                    text = text[:300] + "..."
                print(f"  {key} = {text}", file=sys.stderr)

        output = workflow.run_streaming(question, thread_id=args.thread_id, on_update=_print_update)
    else:
        output = workflow.run(question, thread_id=args.thread_id)

    print(json.dumps(output.model_dump(), ensure_ascii=False, indent=2))
    if output.error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
