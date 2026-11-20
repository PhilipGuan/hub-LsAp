"""命令行入口：`python -m sqlagent.cli "问题"`。

两种后端可切：
- `--route workflow`（默认）：LangGraph 显式状态图 + ChatBI 三道闸门；
- `--route agent`：LangChain `create_agent` 三角色 Agent + 同一套闸门。

跑起来的差异，正好是「编排写在 Python 里」与「把决策权交给模型」两种风格的直接对照。
"""

from __future__ import annotations

import argparse
import sys

from .chatbi import ChatBI, render_result
from .config import ConfigError, get_db_url, redact
from .db import DBParser


def _build_asker(args, parser):
    if args.route == "agent":
        from .agents.sql_agent import SQLAgent
        return SQLAgent(parser, verbose=False).ask
    return ChatBI(parser).ask


def _print_turn(asker, question: str, *, show_trace: bool, render: bool) -> None:
    res = asker(question, show_trace=show_trace)
    print("助手：", res["answer"])
    if render and res.get("rows"):
        render_result(res)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sqlagent", description="SQL Agent · LangChain 版")
    ap.add_argument("question", nargs="?", help="一次性问题；省略则进入交互模式")
    ap.add_argument("--route", choices=["workflow", "agent"], default="workflow",
                    help="后端：workflow=LangGraph 状态图（默认）；agent=create_agent")
    ap.add_argument("--repl", action="store_true", help="进入多轮交互模式")
    ap.add_argument("--trace", action="store_true", help="打印闸门与阶段轨迹")
    ap.add_argument("--db", default="chinook.db", help="数据库文件名（默认 chinook.db）")
    args = ap.parse_args(argv)

    try:
        db_url = get_db_url(args.db)
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2

    parser = DBParser(db_url, verbose=False)
    print(f"数据库：{redact(db_url)}｜表 {len(parser.table_names)} 张｜后端：{args.route}")
    asker = _build_asker(args, parser)

    if args.question:
        _print_turn(asker, args.question, show_trace=args.trace, render=True)
        return 0

    print("输入问题（空行 / exit 退出）：")
    while True:
        try:
            q = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q in ("", "exit", "quit", "退出"):
            break
        _print_turn(asker, q, show_trace=args.trace, render=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
