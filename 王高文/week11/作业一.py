"""OpenAI Agents SDK SQL workflow for the local Chinook SQLite database.

Install with: pip install openai-agents
Set OPENAI_API_KEY and optionally OPENAI_BASE_URL, then run:
    python sql_agent.py "哪些音乐类型的曲目数量最多？"

Workflow: database parsing -> table selection -> SQL generation -> validation
-> bounded SQL rewriting -> read-only execution -> natural-language summary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from agents import (
    Agent,
    Runner,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from agents.decorators import tool
from openai import AsyncOpenAI
from pydantic import BaseModel, Field


HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "chinook.db"
MODEL = os.getenv("OPENAI_MODEL", "mimo-v2.6-pro")
DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
MAX_REWRITES = 2
MAX_RESULT_ROWS = 50
QUERY_TIMEOUT_SECONDS = 10

# This workflow sends schema, the question, SQL, and query results to the model.
# Disable SDK trace export by default; opt in with SQL_AGENT_ENABLE_TRACING=1.
if os.getenv("SQL_AGENT_ENABLE_TRACING", "0") != "1":
    set_tracing_disabled(True)


class SQLAgentError(RuntimeError):
    """An expected, user-facing SQL Agent failure."""


def configure_openai_client(api_key: str, base_url: str) -> None:
    """Configure the API key and endpoint used by all Agents SDK agents."""
    if not api_key.strip():
        raise SQLAgentError("请设置 OPENAI_API_KEY。")
    client = AsyncOpenAI(api_key=api_key.strip(), base_url=base_url.strip())
    set_default_openai_client(client, use_for_tracing=False)


class TableSelection(BaseModel):
    """Structured table-selection result returned by the Agents SDK."""

    tables: list[str] = Field(description="完成查询所需的数据库表名")


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class DatabaseParser:
    """Inspect a SQLite database without changing it."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()
        if not self.db_path.is_file():
            raise SQLAgentError(f"找不到数据库文件：{self.db_path}")
        self.tables: dict[str, dict[str, Any]] = {}
        self.foreign_keys: list[dict[str, str]] = []
        self.parse()

    def connect_readonly(self) -> sqlite3.Connection:
        uri = self.db_path.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def parse(self) -> None:
        with closing(self.connect_readonly()) as conn:
            table_names = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            if not table_names:
                raise SQLAgentError("数据库中没有可用的数据表。")

            for table_name in table_names:
                quoted = quote_identifier(table_name)
                columns = [
                    dict(row)
                    for row in conn.execute(f"PRAGMA table_info({quoted})")
                ]
                foreign_keys = [
                    dict(row)
                    for row in conn.execute(f"PRAGMA foreign_key_list({quoted})")
                ]
                for fk in foreign_keys:
                    self.foreign_keys.append(
                        {
                            "table": table_name,
                            "column": fk["from"],
                            "referenced_table": fk["table"],
                            "referenced_column": fk["to"],
                        }
                    )
                samples = conn.execute(f"SELECT * FROM {quoted} LIMIT 2").fetchall()
                self.tables[table_name] = {
                    "columns": columns,
                    "samples": [dict(row) for row in samples],
                }

    def schema_text(self, table_names: list[str] | None = None) -> str:
        selected = table_names or list(self.tables)
        chunks: list[str] = []
        for table_name in selected:
            info = self.tables[table_name]
            column_lines = []
            for col in info["columns"]:
                marks = []
                if col["pk"]:
                    marks.append("primary key")
                if col["notnull"]:
                    marks.append("not null")
                suffix = f" [{', '.join(marks)}]" if marks else ""
                column_lines.append(f"{col['name']} ({col['type'] or 'untyped'}){suffix}")
            chunks.append(f"Table {table_name}: " + ", ".join(column_lines))
            if info["samples"]:
                chunks.append(
                    "Sample rows: "
                    + json.dumps(info["samples"], ensure_ascii=False, default=str)
                )

        relations = [fk for fk in self.foreign_keys if fk["table"] in selected]
        if relations:
            chunks.append(
                "Foreign keys: "
                + "; ".join(
                    f"{fk['table']}.{fk['column']} -> "
                    f"{fk['referenced_table']}.{fk['referenced_column']}"
                    for fk in relations
                )
            )
        return "\n".join(chunks)


def read_only_authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    database: str | None,
    source: str | None,
) -> int:
    allowed = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def validate_sql(sql: str, db_path: str | Path) -> tuple[bool, str]:
    """Check syntax and reject non-read queries using SQLite's authorizer."""
    candidate = sql.strip()
    if not candidate:
        return False, "SQL 为空。"

    # Ignore leading whitespace and SQL comments when checking the statement type.
    without_comments = re.sub(
        r"\A(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*",
        "",
        candidate,
        flags=re.DOTALL,
    )
    if not re.match(r"(?is)^(SELECT|WITH)\b", without_comments):
        return False, "只允许 SELECT 或 WITH 查询。"

    uri = Path(db_path).expanduser().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    started = time.monotonic()
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.set_authorizer(read_only_authorizer)
        conn.set_progress_handler(
            lambda: int(time.monotonic() - started > QUERY_TIMEOUT_SECONDS), 1000
        )
        # execute() accepts one statement only; EXPLAIN catches missing tables/columns.
        conn.execute("EXPLAIN QUERY PLAN " + candidate).fetchall()
        return True, "SQL 语法及只读权限检查通过。"
    except sqlite3.Error as exc:
        return False, str(exc)
    finally:
        conn.close()


def execute_sql(sql: str, db_path: str | Path) -> dict[str, Any]:
    """Execute a validated query read-only and cap returned rows."""
    valid, error = validate_sql(sql, db_path)
    if not valid:
        raise SQLAgentError(f"SQL 未通过执行前检查：{error}")

    uri = Path(db_path).expanduser().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    started = time.monotonic()
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.set_authorizer(read_only_authorizer)
        conn.set_progress_handler(
            lambda: int(time.monotonic() - started > QUERY_TIMEOUT_SECONDS), 1000
        )
        query = sql.strip().rstrip(";").strip()
        cursor = conn.execute(
            f"SELECT * FROM ({query}) AS agent_result LIMIT ?",
            (MAX_RESULT_ROWS + 1,),
        )
        rows = cursor.fetchall()
        return {
            "columns": [item[0] for item in cursor.description or []],
            "rows": [dict(row) for row in rows[:MAX_RESULT_ROWS]],
            "truncated": len(rows) > MAX_RESULT_ROWS,
        }
    except sqlite3.Error as exc:
        raise SQLAgentError(f"SQL 执行失败：{exc}") from exc
    finally:
        conn.close()


class SQLAgentWorkflow:
    """Run each SQL Agent stage explicitly through OpenAI Agents SDK."""

    def __init__(self, db_path: str | Path = DEFAULT_DB, model: str = MODEL):
        self.parser = DatabaseParser(db_path)
        self.model = model
        self._approved_sql = ""
        self._tool_result: dict[str, Any] | None = None

        parser = self.parser

        @tool
        def inspect_database() -> str:
            """Read database tables, columns, relationships, and sample rows."""
            return parser.schema_text()

        self.table_selector = Agent(
            name="Table Selector",
            model=self.model,
            instructions=(
                "你负责从 Chinook 数据库选择完成用户问题所需的最少数据表。"
                "必须先调用 inspect_database 获取真实 schema。"
                '最后只返回合法 JSON，不要 Markdown，格式为 {"tables":["tracks"]}。'
                "只可选择 schema 中存在的表名。"
            ),
            tools=[inspect_database],
        )
        self.sql_generator = Agent(
            name="SQL Generator",
            model=self.model,
            instructions=(
                "你是 SQLite SQL 专家。根据用户问题和提供的表结构生成一条 SQL。"
                "只输出 SQL，不要解释或使用 Markdown。只能使用提供的表和字段；"
                "必须是只读 SELECT/WITH 查询；列表查询限制到 50 行。"
            ),
        )
        self.sql_rewriter = Agent(
            name="SQL Rewriter",
            model=self.model,
            instructions=(
                "修复 SQLite 查询中的语法、表名、字段名或逻辑错误。"
                "只输出一条只读 SELECT/WITH SQL，不要 Markdown；"
                "仅使用 schema 中存在的表和字段，并保留用户问题的含义。"
            ),
        )
        workflow = self

        @tool
        def execute_approved_query(sql: str) -> str:
            """Execute the validated SQL query against the local read-only database."""
            normalize = lambda value: value.strip().rstrip(";").strip()
            if normalize(sql) != normalize(workflow._approved_sql):
                return "拒绝执行：SQL 与本轮通过校验的查询不一致。"
            workflow._tool_result = execute_sql(sql, workflow.parser.db_path)
            return json.dumps(workflow._tool_result, ensure_ascii=False, default=str)

        self.sql_executor = Agent(
            name="SQL Executor",
            model=self.model,
            instructions=(
                "你是只读 SQL 执行代理。必须且只能调用 execute_approved_query 一次，"
                "参数必须原样使用用户消息中的 SQL。不要改写 SQL，不要直接回答或总结。"
            ),
            tools=[execute_approved_query],
        )
        self.result_summarizer = Agent(
            name="Result Summarizer",
            model=self.model,
            instructions=(
                "用简洁中文回答用户问题，只能依据提供的 SQL 结果陈述事实。"
                "结果为空时明确说明；如果结果被截断，指出当前只展示了前 50 行。"
            ),
        )

    @staticmethod
    def _sql_text(agent_output: Any) -> str:
        text = str(agent_output).strip()
        text = re.sub(r"^```(?:sql)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^sql\s*\n", "", text, flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def _parse_table_selection(agent_output: Any) -> TableSelection:
        text = str(agent_output).strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise SQLAgentError("选表 Agent 没有返回有效 JSON。")
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise SQLAgentError("选表 Agent 返回的 JSON 无法解析。") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("tables"), list):
            raise SQLAgentError('选表结果格式错误，应为 {"tables": [...]}。')
        return TableSelection(tables=payload["tables"])

    def run(self, question: str) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise SQLAgentError("问题不能为空。")

        print("[1/7] 数据库解析完成：", ", ".join(self.parser.tables))

        selection_run = Runner.run_sync(
            self.table_selector,
            f"用户问题：{question}\n\n数据库 schema：\n{self.parser.schema_text()}",
        )
        selection = self._parse_table_selection(selection_run.final_output)
        selected_tables = list(
            dict.fromkeys(
                name for name in selection.tables if name in self.parser.tables
            )
        )
        if not selected_tables:
            raise SQLAgentError("模型没有选择有效的数据表。")
        schema = self.parser.schema_text(selected_tables)
        print("[2/7] 选择的数据表：", ", ".join(selected_tables))

        generation_run = Runner.run_sync(
            self.sql_generator,
            f"用户问题：{question}\n\n已选表 schema：\n{schema}",
        )
        sql = self._sql_text(generation_run.final_output)
        print("[3/7] SQL 已生成。")

        for attempt in range(MAX_REWRITES + 1):
            valid, error = validate_sql(sql, self.parser.db_path)
            if valid:
                print("[4/7] SQL 校验通过。")
                break
            if attempt == MAX_REWRITES:
                raise SQLAgentError(
                    f"SQL 校验失败，已改写 {MAX_REWRITES} 次：{error}\nSQL：{sql}"
                )
            print(f"[4/7] 校验失败，正在第 {attempt + 1} 次改写：{error}")
            rewrite_run = Runner.run_sync(
                self.sql_rewriter,
                f"用户问题：{question}\n\nSchema：\n{schema}\n\n"
                f"原 SQL：\n{sql}\n\nSQLite 校验错误：{error}",
            )
            sql = self._sql_text(rewrite_run.final_output)

        self._approved_sql = sql
        self._tool_result = None
        Runner.run_sync(
            self.sql_executor,
            "调用 execute_approved_query 执行以下原始 SQL，不要改动：\n" + sql,
        )
        if self._tool_result is None:
            raise SQLAgentError("SQL Executor 未调用执行工具，查询没有执行。")
        result = self._tool_result
        print("[5/7] SQL 执行完成，返回行数：", len(result["rows"]))

        summary_run = Runner.run_sync(
            self.result_summarizer,
            f"用户问题：{question}\n执行 SQL：{sql}\n查询结果："
            + json.dumps(result, ensure_ascii=False, default=str),
        )
        summary = str(summary_run.final_output).strip()
        print("[6/7] 结果汇总完成。")
        print("[7/7] Workflow 完成。")
        return {
            "question": question,
            "tables": selected_tables,
            "sql": sql,
            "result": result,
            "summary": summary,
        }


def main() -> int:
    cli = argparse.ArgumentParser(
        description="使用 OpenAI Agents SDK 查询 Chinook SQLite 数据库"
    )
    cli.add_argument("question", nargs="?", help="自然语言问题；省略时进入交互输入")
    cli.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 数据库文件路径")
    cli.add_argument("--model", default=MODEL, help=f"OpenAI 模型（默认：{MODEL}）")
    cli.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL),
        help="API endpoint base URL（也可设置 OPENAI_BASE_URL）",
    )
    cli.add_argument(
        "--api-mode",
        choices=("responses", "chat_completions"),
        default=os.getenv("OPENAI_API_MODE", "chat_completions"),
        help="Agents SDK 请求模式（默认 chat_completions；支持时可设 responses）",
    )
    args = cli.parse_args()

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("错误：请先设置 OPENAI_API_KEY。", file=sys.stderr)
        return 2

    question = args.question or input("请输入数据库问题：").strip()
    try:
        configure_openai_client(api_key, args.base_url)
        set_default_openai_api(args.api_mode)
        response = SQLAgentWorkflow(args.db, args.model).run(question)
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print("\n生成的 SQL：\n" + response["sql"])
    print("\n查询结果：")
    print(json.dumps(response["result"], ensure_ascii=False, indent=2, default=str))
    print("\n回答：\n" + response["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
