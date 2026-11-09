"""CLI 入口（S8）：python -m research "研究主题"。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config, reporter
from .agent import ResearchAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research", description="深度研究助手：输入主题，产出带来源引用的研究报告"
    )
    parser.add_argument("topic", nargs="+", help="研究主题")
    parser.add_argument("--max-rounds", type=int, default=None, help="覆盖最大检索轮次")
    parser.add_argument("--out", type=str, default=None, help="输出根目录（默认 output/）")
    parser.add_argument("--quiet", action="store_true", help="不打印过程进度")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    topic = " ".join(args.topic).strip()
    if not topic:
        print("错误：研究主题不能为空", file=sys.stderr)
        return 2
    try:
        config.require_keys()
    except RuntimeError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    if args.max_rounds:
        config.MAX_ROUNDS = args.max_rounds

    out_dir = None if args.out is None else reporter.make_out_dir(topic, root=Path(args.out))
    try:
        agent = ResearchAgent(topic, out_dir=out_dir, verbose=not args.quiet)
        result = agent.run()
    except RuntimeError as e:
        print(f"\n错误：{e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130

    conclusions = result.report.key_conclusions
    with_source = sum(1 for c in conclusions if c.refs)
    print("\n===== 研究完成 =====")
    print(f"输出目录：{result.out_dir}")
    print(
        f"迭代轮次：{result.rounds}｜来源：{len(result.registry.all_sources())}"
        f"｜材料：{len(result.registry.findings)}｜耗时：{result.elapsed_sec:.0f} 秒"
    )
    if conclusions:
        print(
            f"关键结论：{len(conclusions)} 条，带来源 {with_source} 条"
            f"（{with_source / len(conclusions):.0%}）"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
