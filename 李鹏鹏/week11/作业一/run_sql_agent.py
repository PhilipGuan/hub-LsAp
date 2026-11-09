"""SQL Agent 命令行入口。

用法：
    # 单次提问
    python run_sql_agent.py "哪个流派的曲目数量最多？"

    # 指定表（跳过 table 选择）
    python run_sql_agent.py "总销售额是多少？" --tables invoices

    # 运行内置示例
    python run_sql_agent.py --demo

    # 查看数据库结构
    python run_sql_agent.py --schema
    python run_sql_agent.py --describe invoices

    # 交互式问答
    python run_sql_agent.py --interactive
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，统一改用 UTF-8，避免中文输出乱码
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from sql_agent.config import ConfigError, Settings
from sql_agent.db_parser import DBParser
from sql_agent.llm import LLMError
from sql_agent.workflow import SQLAgentResult, SQLAgentWorkflow, StepTrace

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "chinook.db"

DEMO_QUESTIONS = [
    "哪个流派的曲目数量最多？列出前 5 个流派及其曲目数。",
    "2009 年每个月的总销售额是多少？按月从早到晚排序。",
    "消费金额最高的 5 位客户是谁？给出客户姓名和总消费金额。",
]


def _print_trace(trace: StepTrace, index: int) -> None:
    detail = trace.detail.strip()
    if len(detail) > 300:
        detail = detail[:297] + "..."
    if "\n" in detail:
        detail = detail.replace("\n", "\n      ")
    print(f"  [{index}] {trace.name:<10} {trace.elapsed:6.2f}s  {detail}")


def print_result(
    result: SQLAgentResult,
    show_rows: int = 10,
    show_traces: bool = True,
    show_header: bool = True,
) -> None:
    if show_header:
        print("\n" + "=" * 72)
        print(f"问题：{result.question}")
        print("=" * 72)

    if show_traces:
        print("\n工作流执行过程：")
        for index, trace in enumerate(result.traces, start=1):
            _print_trace(trace, index)

    print("\n最终 SQL：")
    print(result.sql or "（无）")

    if result.columns:
        print(f"\n查询结果（{len(result.rows)} 行"
              + ("，已截断" if result.truncated else "") + "）：")
        widths = [len(str(c)) for c in result.columns]
        display_rows = result.rows[:show_rows]
        for row in display_rows:
            for i, value in enumerate(row):
                widths[i] = max(widths[i], len(str(value)))

        def fmt(values) -> str:
            return " | ".join(str(v).ljust(widths[i]) for i, v in enumerate(values))

        print(fmt(result.columns))
        print("-+-".join("-" * w for w in widths))
        for row in display_rows:
            print(fmt(row))
        if len(result.rows) > show_rows:
            print(f"... 其余 {len(result.rows) - show_rows} 行已省略")

    print("\n答案：")
    print(result.answer or "（无）")

    if result.error:
        print(f"\n错误：{result.error}")
    if result.llm_usage:
        usage = result.llm_usage
        print(
            f"\n模型调用：{usage.get('calls', 0)} 次，"
            f"tokens：{usage.get('total_tokens', 0)}"
            f"（prompt {usage.get('prompt_tokens', 0)} / "
            f"completion {usage.get('completion_tokens', 0)}），"
            f"模型：{usage.get('model', '')}"
        )
    print()


def print_schema(db_path: Path) -> None:
    with DBParser(db_path) as parser:
        parser.parse_all()
        print("表清单：")
        print(parser.table_overview_markdown())
        print("\n外键关系：")
        print(parser.relations_markdown())


def print_table_schema(db_path: Path, table_name: str) -> None:
    with DBParser(db_path) as parser:
        print(parser.table_schema_markdown(table_name))


def run_one(
    workflow: SQLAgentWorkflow,
    question: str,
    tables: list[str] | None,
    as_json: bool,
    show_rows: int,
) -> SQLAgentResult:
    step_index = {"n": 0}

    def on_step(trace: StepTrace) -> None:
        if as_json:
            return
        step_index["n"] += 1
        _print_trace(trace, step_index["n"])
        sys.stdout.flush()

    if not as_json:
        print("\n" + "=" * 72)
        print(f"问题：{question}")
        print("=" * 72)
        print("\n工作流执行过程：")

    workflow.on_step = on_step
    result = workflow.run(question, tables=tables)

    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print_result(
            result, show_rows=show_rows, show_traces=False, show_header=False
        )
    return result


def main(argv: list[str] | None = None) -> int:
    arg_parser = argparse.ArgumentParser(
        description="SQL Agent：自然语言 -> SQL -> 自然语言回答"
    )
    arg_parser.add_argument("question", nargs="*", help="要查询的问题")
    arg_parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 数据库路径")
    arg_parser.add_argument(
        "--tables", default="", help="指定表名，逗号分隔（跳过 table 选择）"
    )
    arg_parser.add_argument("--max-rows", type=int, default=None, help="最大返回行数")
    arg_parser.add_argument("--max-repair", type=int, default=None, help="最大改写次数")
    arg_parser.add_argument("--demo", action="store_true", help="运行内置示例问题")
    arg_parser.add_argument("--schema", action="store_true", help="打印数据库结构")
    arg_parser.add_argument("--describe", default="", help="打印单表详细结构")
    arg_parser.add_argument(
        "--sql", default="", help="直接校验并执行给定 SQL（不调用大模型）"
    )
    arg_parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    arg_parser.add_argument("--interactive", action="store_true", help="交互式问答")
    arg_parser.add_argument("--show-rows", type=int, default=10, help="终端展示行数")
    args = arg_parser.parse_args(argv)

    db_path = Path(args.db).expanduser().resolve()
    if not db_path.is_file():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    if args.schema:
        print_schema(db_path)
        return 0

    if args.describe:
        print_table_schema(db_path, args.describe)
        return 0

    if args.sql:
        with DBParser(db_path) as parser:
            ok, message = parser.validate_sql(args.sql)
            print(f"校验：{'通过' if ok else '失败'} - {message}")
            if not ok:
                return 1
            columns, rows, truncated = parser.execute_sql(
                args.sql, max_rows=args.max_rows or 100
            )
            print(f"执行：{len(rows)} 行" + ("（已截断）" if truncated else ""))
            print(" | ".join(columns))
            for row in rows[: args.show_rows]:
                print(" | ".join(str(value) for value in row))
        return 0

    settings = Settings.load()
    if args.max_rows is not None:
        settings.max_rows = max(1, args.max_rows)
    if args.max_repair is not None:
        settings.max_repair = max(0, args.max_repair)

    specify_tables = [t.strip() for t in args.tables.split(",") if t.strip()]

    questions: list[str] = []
    if args.demo:
        questions = list(DEMO_QUESTIONS)
    elif args.question:
        questions = [" ".join(args.question)]
    elif args.interactive or sys.stdin.isatty():
        questions = []
    else:
        arg_parser.print_help()
        return 0

    try:
        with SQLAgentWorkflow(db_path, settings=settings) as workflow:
            if questions:
                failed = 0
                for question in questions:
                    result = run_one(
                        workflow,
                        question,
                        specify_tables or None,
                        args.json,
                        args.show_rows,
                    )
                    failed += 0 if result.ok else 1
                return 0 if failed == 0 else 1

            # 交互模式
            print("SQL Agent 交互模式，输入 q / exit 退出。")
            while True:
                try:
                    question = input("\n问题> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if question.lower() in {"q", "quit", "exit"}:
                    break
                if not question:
                    continue
                run_one(
                    workflow,
                    question,
                    specify_tables or None,
                    args.json,
                    args.show_rows,
                )
            return 0

    except (ConfigError, LLMError) as exc:
        print(f"\n配置错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
