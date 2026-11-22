"""命令行入口：`python -m sqlagent.cli "问题"`。

三种后端可切：

- `--route workflow`（默认）：LangGraph 显式状态图 + ChatBI 三道闸门；
- `--route agent`：LangChain `create_agent` 三角色 Agent + 同一套闸门；
- **进阶会话图**（`ConversationBI`）：只要带上 `--thread-id / --review / --trajectory / --replay`
  任一参数即启用。它把三道闸门也纳入 LangGraph，从而支持三件套——

  - 持久化（`--thread-id` + `--checkpointer`）：多轮记忆随 `thread_id` 落库；
  - 人工介入（`--review`）：SQL 生成后暂停，等 approve / edit / reject；
  - 时间旅行（`--trajectory` / `--replay <checkpoint_id>`）：打印检查点轨迹、从任意一跳重跑。

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
        return SQLAgent(parser, verbose=False, lang=args.lang).ask
    return ChatBI(parser, lang=args.lang).ask


def _print_turn(asker, question: str, *, show_trace: bool, render: bool) -> None:
    res = asker(question, show_trace=show_trace)
    print("助手：", res["answer"])
    if render and res.get("rows"):
        render_result(res)


# --------------------------------------------------------------------------- #
# 进阶路径（会话图 ConversationBI）
# --------------------------------------------------------------------------- #

def _console_reviewer(payload: dict) -> dict:
    """交互式人工复核：打印待审 SQL，读一个决定（非交互环境默认放行）。"""
    print("\n[人工复核] 待审 SQL：", payload.get("sql"))
    print("（approve=放行 / edit=改写 / reject=驳回）")
    try:
        answer = input("决定 [approve]：").strip() or "approve"
    except (EOFError, KeyboardInterrupt):
        print("（非交互环境，默认放行）")
        return {"action": "approve"}
    if answer.startswith("e"):
        try:
            new_sql = input("请输入 SQL：").strip()
        except (EOFError, KeyboardInterrupt):
            new_sql = ""
        return {"action": "edit", "sql": new_sql}
    if answer.startswith("r"):
        try:
            note = input("驳回理由（可空）：").strip()
        except (EOFError, KeyboardInterrupt):
            note = ""
        return {"action": "reject", "note": note or None}
    return {"action": "approve"}


def _drive(bot, question: str) -> dict:
    """发起一轮提问；若图暂停（澄清 / 复核），就在此把答复送回去，直到图跑完。"""
    trace = bot.ask(question)
    while trace.get("status") == "interrupted":
        payload = trace.get("interrupt") or {}
        if payload.get("type") == "clarify":
            print("助手（澄清）：", payload.get("question"))
            try:
                answer = input("你：").strip()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            trace = bot.resume(answer)
        else:
            trace = bot.resume(_console_reviewer(payload))
    return trace


def _build_advanced(args, parser):
    from .conversation import ConversationBI
    return ConversationBI(
        parser,
        thread_id=args.thread_id or None,
        checkpointer_kind=args.checkpointer,
        checkpointer_path=args.checkpoint_path,
        human_review=args.review,
        verbose=args.trace,
        lang=args.lang,
    )


def _print_advanced_turn(bot, question: str, args) -> dict:
    from .time_travel import format_hops, list_hops

    trace = _drive(bot, question)
    print("助手：", trace.get("answer"))
    if trace.get("rows"):
        render_result(trace)
    if args.trajectory:
        print("\n" + format_hops(list_hops(bot.graph, bot.config)))
    return trace


def _do_replay(args, bot) -> None:
    from .time_travel import replay_from

    print(f"从检查点回放：{args.replay}")
    final = replay_from(bot.graph, bot.config, args.replay)
    print("—— 回放后 SQL ：", final.get("sql"))
    print("—— 回放后回答：", final.get("answer"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sqlagent", description="SQL Agent · LangGraph 进阶版")
    ap.add_argument("question", nargs="?", help="一次性问题；省略则进入交互模式")
    ap.add_argument("--route", choices=["workflow", "agent"], default="workflow",
                    help="后端：workflow=LangGraph 状态图（默认）；agent=create_agent")
    ap.add_argument("--repl", action="store_true", help="进入多轮交互模式")
    ap.add_argument("--trace", action="store_true", help="打印闸门与阶段轨迹")
    ap.add_argument("--db", default="chinook.db", help="数据库文件名（默认 chinook.db）")
    # —— 进阶三件套 ——
    ap.add_argument("--thread-id", default=None,
                    help="会话线程 ID；指定后启用会话图（持久化 / 可中断）")
    ap.add_argument("--checkpointer", choices=["memory", "sqlite"], default="memory",
                    help="持久化后端：memory=InMemorySaver（默认）；sqlite=落盘")
    ap.add_argument("--checkpoint-path", default="checkpoints.sqlite",
                    help="sqlite 检查点文件路径（仅 --checkpointer sqlite）")
    ap.add_argument("--review", action="store_true", help="开启人工复核（生成 SQL 后暂停等决定）")
    ap.add_argument("--trajectory", action="store_true", help="跑完打印检查点轨迹（时间旅行）")
    ap.add_argument("--replay", metavar="CHECKPOINT_ID", default=None,
                    help="从指定检查点回放（需配合 --thread-id）")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh",
                    help="面向用户话术与结论的语言（默认 zh；en=英文）")
    args = ap.parse_args(argv)

    advanced = bool(args.thread_id or args.review or args.trajectory or args.replay)
    if advanced and args.route != "workflow":
        print("提示：--thread-id/--review/--trajectory/--replay 仅适用于 workflow 路线。",
              file=sys.stderr)
        return 2
    if args.replay and not args.thread_id:
        print("提示：--replay 需要一个已有历史的会话，请同时指定 --thread-id。", file=sys.stderr)
        return 2

    try:
        db_url = get_db_url(args.db)
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2

    parser = DBParser(db_url, verbose=False)

    # ---------------------------- 进阶路径 ---------------------------- #
    if advanced:
        bot = _build_advanced(args, parser)
        print(f"数据库：{redact(db_url)}｜表 {len(parser.table_names)} 张｜后端：会话图 (ConversationBI)")
        print(f"会话线程：{bot.thread_id}｜检查点：{args.checkpointer}"
              + (f"（{args.checkpoint_path}）" if args.checkpointer == "sqlite" else "")
              + (f"｜人工复核：开" if args.review else ""))

        if args.replay:
            _do_replay(args, bot)
            return 0

        if args.question:
            _print_advanced_turn(bot, args.question, args)
            return 0

        print("输入问题（空行 / exit 退出）：")
        while True:
            try:
                q = input("你：").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q in ("", "exit", "quit", "退出"):
                break
            _print_advanced_turn(bot, q, args)
        return 0

    # ---------------------------- 基线路径 ---------------------------- #
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
