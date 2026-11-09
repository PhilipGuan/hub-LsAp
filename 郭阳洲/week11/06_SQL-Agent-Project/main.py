from __future__ import annotations

import argparse
import json
import sys

from sql_agent.agent import AgentResult, SQLAgent
from sql_agent.config import settings
from sql_agent.trace_store import normalize_user_code


def print_result(result: AgentResult) -> None:
    print("=" * 72)
    print(f"问题：{result.question}")
    print("=" * 72)
    for step in result.steps:
        mark = "OK " if step.status == "ok" else "ERR"
        print(f"[{mark}] {step.name}  ({step.elapsed_ms:.0f} ms)")
        for line in step.detail.splitlines():
            print(f"    {line}")
        print()

    if result.sql:
        print("最终 SQL：")
        print(result.sql)
        print()

    if result.columns and result.rows:
        print("结果数据：")
        print(result.to_dataframe().to_string(index=False))
        if result.truncated:
            print(f"（已截断，仅展示前 {len(result.rows)} 行）")
        print()

    print("答案：")
    print(result.answer if result.success else f"失败：{result.error}")

    meta = []
    if result.metric and result.metric.get("label"):
        meta.append(f"指标：{result.metric['label']}")
    if result.time_range and result.time_range.get("label"):
        meta.append(f"时间：{result.time_range['label']}")
    if result.data_until:
        meta.append(f"数据截止：{result.data_until}")
    if result.trace_id:
        meta.append(f"trace_id：{result.trace_id}")
    if meta:
        print("\n" + " · ".join(meta))

    if result.error_code:
        print(
            f"错误码：{result.error_code} {result.error_name}"
            f"（error_class={result.error_class}）"
        )
    if result.warnings:
        print("告警：")
        for warning in result.warnings:
            print(
                f"  - [{warning.get('severity', 'info')}] "
                f"{warning.get('code', '')} {warning.get('detail', '')}"
            )
    if result.assumptions:
        print("口径与假设：")
        for item in result.assumptions:
            print(f"  - {item}")
    if result.budget:
        budget = result.budget
        print(
            f"\nLLM 调用 {budget.get('calls', 0)} 次 / "
            f"{budget.get('total_tokens', 0)} tokens / "
            f"￥{budget.get('cost_cny', 0):.4f}"
        )
    print(f"总耗时：{result.total_ms:.0f} ms，尝试次数：{result.attempts}")
    print()


def run_question(agent: SQLAgent, question: str, user_code: str, as_json: bool) -> None:
    result = agent.run(question, user_code=user_code)
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, default=str))
    else:
        print_result(result)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Chinook SQL Agent")
    parser.add_argument("question", nargs="?", help="中文问题")
    parser.add_argument("-i", "--interactive", action="store_true", help="交互模式")
    parser.add_argument(
        "--user-code",
        default="local",
        help="试点用户码（2-16 位字母、数字、下划线或短横线），用于关联反馈与审计。",
    )
    parser.add_argument(
        "--json", action="store_true", help="以 JSON 输出完整结果（含执行过程）"
    )
    args = parser.parse_args()

    try:
        user_code = normalize_user_code(args.user_code)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    agent = SQLAgent()

    if args.interactive:
        print(f"输入问题，输入 exit 退出。当前用户码：{user_code}")
        while True:
            try:
                question = input("\n问题> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if question.lower() in {"exit", "quit", "q"}:
                break
            if question:
                run_question(agent, question, user_code, args.json)
        return 0

    if not args.question:
        parser.print_help()
        return 0

    result = agent.run(args.question, user_code=user_code)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, default=str))
    else:
        print_result(result)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
