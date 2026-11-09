"""CLI entry: python main.py "研究主题" -o report.md"""
import argparse
import os
import sys

from research.llm import LLM
from research.orchestrator import research
from research.search import BochaSearcher


def main() -> None:
    ap = argparse.ArgumentParser(description="深度研究助手 — Deep Research Assistant")
    ap.add_argument("topic", help="研究主题,例如:2026 年具身智能机器人行业趋势")
    ap.add_argument("-o", "--output", default="-", help="输出文件路径,默认 stdout")
    ap.add_argument("--per-question", type=int, default=8, help="每个子问题的检索条数")
    args = ap.parse_args()

    if not os.environ.get("BOCHA_API_KEY"):
        sys.exit("缺少 BOCHA_API_KEY 环境变量")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("缺少 ANTHROPIC_API_KEY 环境变量")

    report = research(args.topic, BochaSearcher(), LLM(), per_question_count=args.per_question)
    md = report.to_markdown()

    if args.output == "-":
        sys.stdout.write(md)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"已写入 {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()