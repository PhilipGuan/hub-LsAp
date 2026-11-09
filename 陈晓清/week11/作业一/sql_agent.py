"""SQL Agent 
    python sql_agent.py "数据库里一共有多少张专辑？"
    python sql_agent.py --demo
    python sql_agent.py            # 进入交互模式
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import traceback
from pathlib import Path
from typing import Any, Union

import pandas as pd
from dotenv import load_dotenv
from agents import (
    Agent,
    AsyncOpenAI,
    ModelSettings,
    OpenAIChatCompletionsModel,
    Runner,
    function_tool,
    set_tracing_disabled,
)
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine

ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_PATH)  # 读取 .env 中的大模型配置

DEFAULT_DB_PATH = Path(__file__).with_name("chinook.db")
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"


# ---------------------------------------------------------------------------
# 1. 数据库解析
# ---------------------------------------------------------------------------
class DBParser:
    """解析数据库结构，并提供 SQL 校验与执行能力。"""

    def __init__(self, db_url: str, verbose: bool = True) -> None:
        """
        db_url:
            sqlite: sqlite:///D:/path/chinook.db
            mysql:  mysql://root:123456@localhost:3306/mydb?charset=utf8mb4
        """
        if "sqlite" in db_url:
            self.db_type = "sqlite"
        elif "mysql" in db_url:
            self.db_type = "mysql"
        else:
            self.db_type = "unknown"

        self.db_url = db_url
        self.engine: Engine = create_engine(db_url, echo=False)
        self.conn = self.engine.connect()
        self.inspector = inspect(self.engine)

        # 忽略 sqlite 内部表（sqlite_sequence / sqlite_stat1 等）
        self.table_names = [
            name for name in self.inspector.get_table_names()
            if not name.startswith("sqlite_")
        ]

        self._table_fields: dict[str, dict[str, dict[str, Any]]] = {}
        self._table_sample: dict[str, pd.DataFrame] = {}
        self._table_rows: dict[str, int] = {}
        self.foreign_keys: list[dict[str, Any]] = []

        for table_name in self.table_names:
            if verbose:
                print(f"Table -> {table_name}")
            self._table_fields[table_name] = {}

            # 外键关系
            self.foreign_keys += [
                {
                    "constrained_table": table_name,
                    "constrained_columns": fk["constrained_columns"],
                    "referred_table": fk["referred_table"],
                    "referred_columns": fk["referred_columns"],
                }
                for fk in self.inspector.get_foreign_keys(table_name)
            ]

            table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
            table_columns = self.inspector.get_columns(table_name)

            # 行数
            self._table_rows[table_name] = self.conn.execute(
                select(func.count()).select_from(table_instance)
            ).fetchone()[0]

            for column_meta in table_columns:
                column_name = column_meta["name"]
                column_instance = getattr(table_instance.columns, column_name)
                meta = dict(column_meta)
                self._table_fields[table_name][column_name] = meta

                # 唯一值个数
                meta["distinct"] = self.conn.execute(
                    select(func.count(func.distinct(column_instance)))
                ).fetchone()[0]

                # 缺失值个数
                meta["nan_count"] = self.conn.execute(
                    select(func.count()).filter(column_instance.is_(None))
                ).fetchone()[0]

                # 最大/最小值
                meta["max"] = self.conn.execute(
                    select(func.max(column_instance))
                ).fetchone()[0]
                meta["min"] = self.conn.execute(
                    select(func.min(column_instance))
                ).fetchone()[0]

                # 文本字段的众数和示例值
                field_type = str(meta["type"]).lower()
                if "text" in field_type or "char" in field_type:
                    meta["mode"] = self.conn.execute(
                        select(column_instance, func.count().label("count"))
                        .group_by(column_instance)
                        .order_by(func.count().desc())
                        .limit(1)
                    ).fetchone()[0]

                values = self.conn.execute(
                    select(column_instance).limit(10)
                ).all()
                values = {str(row[0]) for row in values if row[0] is not None}
                meta["random"] = list(values)[:3]

            # 表样例（前 3 行）
            self._table_sample[table_name] = pd.read_sql(
                select(table_instance).limit(3), self.conn
            )

    # ------------------------- 基础信息 -------------------------
    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        """获取表字段信息。"""
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        """获取数据库外键关系。"""
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str) -> pd.DataFrame:
        """获取表样例数据。"""
        return self._table_sample[table_name]

    def get_tables_overview(self, max_sample: int = 2) -> str:
        """所有表的精简结构，用于表选择阶段。"""
        fk_map = {}
        for fk in self.foreign_keys:
            for src, dst in zip(fk["constrained_columns"], fk["referred_columns"]):
                fk_map[(fk["constrained_table"], src)] = f"{fk['referred_table']}.{dst}"

        lines = []
        for table_name in self.table_names:
            columns = []
            for column_name, meta in self._table_fields[table_name].items():
                part = f"{column_name} {meta['type']}"
                if meta.get("primary_key"):
                    part += " PK"
                fk_target = fk_map.get((table_name, column_name))
                if fk_target:
                    part += f" -> {fk_target}"
                columns.append(part)
            lines.append(f"- {table_name}({', '.join(columns)})")
        return "\n".join(lines)

    def get_table_schema(self, table_name: str) -> str:
        """单张表的详细结构（含统计信息和示例），用于 SQL 生成。"""
        fk_map = {}
        for fk in self.foreign_keys:
            if fk["constrained_table"] != table_name:
                continue
            for src, dst in zip(fk["constrained_columns"], fk["referred_columns"]):
                fk_map[src] = f"{fk['referred_table']}.{dst}"

        lines = [f"### 表 {table_name}（共 {self._table_rows[table_name]} 行）"]
        for column_name, meta in self._table_fields[table_name].items():
            flags = []
            if meta.get("primary_key"):
                flags.append("主键")
            if not meta.get("nullable", True):
                flags.append("非空")
            if column_name in fk_map:
                flags.append(f"外键 -> {fk_map[column_name]}")

            details = [
                f"distinct={meta.get('distinct')}",
                f"null={meta.get('nan_count')}",
            ]
            if meta.get("min") is not None or meta.get("max") is not None:
                details.append(f"范围={self._short(meta.get('min'))}~{self._short(meta.get('max'))}")
            if "mode" in meta and meta["mode"] is not None:
                details.append(f"众数={self._short(meta['mode'])}")
            if meta.get("random"):
                details.append("示例=" + ", ".join(self._short(v) for v in meta["random"]))

            flag_text = f" [{', '.join(flags)}]" if flags else ""
            lines.append(
                f"- {column_name} ({meta['type']}{flag_text}): " + "; ".join(details)
            )
        return "\n".join(lines)

    def get_relations_text(self, table_names: list[str]) -> str:
        """与选中表相关的外键关系。"""
        selected = set(table_names)
        lines = []
        for fk in self.foreign_keys:
            if fk["constrained_table"] not in selected and fk["referred_table"] not in selected:
                continue
            for src, dst in zip(fk["constrained_columns"], fk["referred_columns"]):
                lines.append(f"- {fk['constrained_table']}.{src} -> {fk['referred_table']}.{dst}")
        return "\n".join(lines) if lines else "（无）"

    def get_samples_text(self, table_names: list[str]) -> str:
        """选中表的样例数据。"""
        blocks = []
        for table_name in table_names:
            sample = self.get_table_sample(table_name).head(3)
            blocks.append(f"#### {table_name}\n{sample.to_markdown(index=False)}")
        return "\n\n".join(blocks)

    @staticmethod
    def _short(value: Any, max_len: int = 40) -> str:
        text_value = str(value).replace("\n", " ")
        return text_value if len(text_value) <= max_len else text_value[:max_len] + "..."

    # ------------------------- SQL 校验与执行 -------------------------
    @staticmethod
    def is_read_only(sql: str) -> bool:
        """只允许 SELECT / WITH 查询，防止大模型生成写操作。"""
        cleaned = re.sub(r"--.*?$|/\*.*?\*/", "", sql, flags=re.MULTILINE | re.DOTALL)
        cleaned = re.sub(r"'(?:''|[^'])*'", "''", cleaned).strip()  # 忽略字符串字面量
        if not cleaned:
            return False
        if not re.match(r"(?is)^(select|with)\b", cleaned):
            return False
        forbidden = r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex)\b"
        return re.search(forbidden, cleaned, flags=re.IGNORECASE) is None

    def check_sql(self, sql: str) -> tuple[bool, str]:
        """检查 SQL 是否可执行：只读校验 + 事务内试执行后回滚。"""
        if not self.is_read_only(sql):
            return False, "只允许执行 SELECT / WITH 只读查询，且不能包含写操作关键字。"

        try:
            with self.engine.connect() as conn:
                conn.execute(text(sql))
                conn.rollback()  # 校验不产生任何副作用
            return True, "ok"
        except Exception:
            return False, traceback.format_exc()

    def execute_sql(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        """执行只读 SQL，返回 (列名, 数据行)。"""
        if not self.is_read_only(sql):
            raise ValueError("只允许执行 SELECT / WITH 只读查询。")

        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = [list(row) for row in result]
        return columns, rows

    def close(self) -> None:
        self.conn.close()
        self.engine.dispose()


# ---------------------------------------------------------------------------
# 2~7. SQL Agent
# ---------------------------------------------------------------------------
class SQLAgent:
    """数据库问答 Agent（基于 OpenAI Agents SDK）。

    由 4 个专用 Agent 组成：
        - SQL Table Selector：表选择，可调用 parse_table / get_relations 工具
        - SQL Generator：生成 SQL
        - SQL Rewriter：根据报错改写 SQL
        - SQL Result Summarizer：汇总查询结果为自然语言
    校验与执行由本地 Python 代码负责，失败时驱动 SQL Rewriter 重试。
    """

    _select_instructions = """你是一个专业的数据库专家。请根据用户问题，从候选表中选择回答问题所需的最少表集合。
如果需要 JOIN，请把中间表和关联表一起选出来；只允许从候选表名中选择。
你可以调用 parse_table 工具查看某张表的详细字段和统计信息，调用 get_relations 工具查看外键关系。

候选表结构：
{#overview#}

只输出 JSON，格式如下（不要输出其他内容）：
{"tables": ["表名1", "表名2"], "reason": "选择理由"}
"""

    _generate_instructions = """你是一个专业的 SQLite SQL 专家。请根据用户输入中的问题、表结构、外键关系和样例数据，生成一条可直接执行的查询。

要求：
1. 只能使用给出的表和字段，字段名必须与 schema 完全一致；
2. 只生成一条 SELECT 语句，不要输出解释；
3. 需要聚合时使用 GROUP BY，排序使用 ORDER BY；
4. 使用 SQLite 语法，表名或字段名有特殊字符时使用双引号；
5. 除非用户明确要求全部数据，否则用 LIMIT 控制返回行数。

只输出 SQL，可以用 ```sql 代码块包裹。
"""

    _rewrite_instructions = """你是一个专业的 SQLite SQL 专家。用户提供的 SQL 无法通过校验或执行失败，请修复它。

要求：
1. 结合用户问题、表结构、外键关系和错误信息分析原因；
2. 只使用给出的表和字段；
3. 只生成一条 SELECT 查询，不要输出解释；
4. 使用 SQLite 语法。

只输出修正后的完整 SQL，可以用 ```sql 代码块包裹。
"""

    _summary_instructions = """你是一个数据分析专家。请根据用户输入中的问题、执行的 SQL 和查询结果，用自然语言给出最终回答。

要求：
1. 直接回答用户问题，突出关键数字和结论；
2. 如果结果有多行，概括主要趋势或列出要点，不要逐行复述；
3. 如果结果为空，说明没有查询到符合条件的数据；
4. 不要输出 SQL 语句本身。
"""

    def __init__(
        self,
        db_path: Union[str, Path] = DEFAULT_DB_PATH,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_retry: int = 5,
        temperature: float = 0.0,
        verbose: bool = True,
    ) -> None:
        self.db_path = Path(db_path).resolve()
        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库文件不存在：{self.db_path}")

        self.verbose = verbose
        self.parser = DBParser(f"sqlite:///{self.db_path.as_posix()}", verbose=verbose)

        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                f"未在 .env 中配置 OPENAI_API_KEY，请编辑：{ENV_PATH}\n"
                "  OPENAI_API_KEY=sk-xxx"
            )
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL)
        self.max_retry = max_retry
        self.temperature = temperature
        self.trace: list[dict[str, Any]] = []

        self._setup_agents()

    def _setup_agents(self) -> None:
        """创建底层模型客户端和 4 个专用 Agent。"""
        set_tracing_disabled(True)  # 使用第三方 OpenAI 兼容接口时关闭 tracing 上传

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        agents_model = OpenAIChatCompletionsModel(
            model=self.model,
            openai_client=client,
        )
        model_settings = ModelSettings(temperature=self.temperature, top_p=0.7)

        @function_tool
        def parse_table(table_name: str) -> str:
            """查看指定表的字段、类型、主键、外键、统计信息和示例值。table_name 必须是候选表名。"""
            if table_name not in self.parser.table_names:
                return f"表 {table_name} 不存在。可选择的表有：{self.parser.table_names}"
            return self.parser.get_table_schema(table_name)

        @function_tool
        def get_relations() -> str:
            """查看数据库所有表之间的外键关系，用于确定 JOIN 条件。"""
            return self.parser.get_relations_text(self.parser.table_names)

        self.table_select_agent = Agent(
            name="SQL Table Selector",
            instructions=self._select_instructions.replace(
                "{#overview#}", self.parser.get_tables_overview()
            ),
            tools=[parse_table, get_relations],
            model=agents_model,
            model_settings=model_settings,
        )
        self.sql_generator_agent = Agent(
            name="SQL Generator",
            instructions=self._generate_instructions,
            model=agents_model,
            model_settings=model_settings,
        )
        self.sql_rewriter_agent = Agent(
            name="SQL Rewriter",
            instructions=self._rewrite_instructions,
            model=agents_model,
            model_settings=model_settings,
        )
        self.summary_agent = Agent(
            name="SQL Result Summarizer",
            instructions=self._summary_instructions,
            model=agents_model,
            model_settings=model_settings,
        )

    def _log(self, step: str, message: str) -> None:
        self.trace.append({"step": step, "message": message})
        if self.verbose:
            print(f"[{step}] {message}")

    @staticmethod
    def extract_sql(text_value: str) -> str:
        """从 Agent 输出中抽取 SQL。"""
        if not text_value:
            return ""
        match = re.search(r"```(?:sql)?\s*(.*?)```", text_value, flags=re.IGNORECASE | re.DOTALL)
        if match:
            sql = match.group(1)
        else:
            # 去掉常见的 "SQL:" 前缀，从第一个 SELECT / WITH 开始截取
            match = re.search(r"(?is)\b(select|with)\b.*", text_value)
            sql = match.group(0) if match else text_value
        sql = sql.strip().strip("`").strip()
        sql = re.sub(r"^(sql|sqlite)\s*[:：]\s*", "", sql, flags=re.IGNORECASE)
        return sql.strip()

    async def _run_agent(self, agent: Agent, input_text: str, step: str) -> str:
        """运行一个 Agent，并返回最终文本输出。"""
        try:
            result = await Runner.run(agent, input_text)
        except Exception as exc:
            raise RuntimeError(f"{step} Agent 执行失败：{exc}") from exc
        return str(result.final_output or "")

    # ------------------------- 2. 表选择 -------------------------
    async def select_tables(self, question: str) -> list[str]:
        raw = await self._run_agent(
            self.table_select_agent,
            f"用户问题：{question}",
            "表选择",
        )
        tables = self._parse_tables(raw)
        if not tables:
            tables = list(self.parser.table_names)
            self._log("table_select", f"Agent 未返回有效表名，回退到全部表：{tables}")
        else:
            self._log("table_select", f"选中表：{tables}")
        return tables

    def _parse_tables(self, raw: str) -> list[str]:
        allowed = self.parser.table_names
        text_value = raw.strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text_value, flags=re.DOTALL)
        if fence:
            text_value = fence.group(1).strip()
        try:
            data = json.loads(text_value)
        except Exception:
            brace = re.search(r"\{.*\}", text_value, flags=re.DOTALL)
            data = None
            if brace:
                try:
                    data = json.loads(brace.group(0))
                except Exception:
                    data = None
        if isinstance(data, dict):
            data = data.get("tables") or data.get("table") or []
        if not isinstance(data, list):
            data = []

        tables = [name for name in data if isinstance(name, str) and name in allowed]
        if tables:
            # 去重并保持 Agent 给出的顺序
            return list(dict.fromkeys(tables))
        # 兜底：从文本中提取真实表名
        return [name for name in allowed if re.search(rf"\b{re.escape(name)}\b", raw)]

    # ------------------------- 3. SQL 生成 -------------------------
    async def generate_sql(self, question: str, tables: list[str]) -> str:
        schema = "\n\n".join(self.parser.get_table_schema(t) for t in tables)
        input_text = (
            f"用户问题：\n{question}\n\n"
            f"选中的表结构：\n{schema}\n\n"
            f"外键关系：\n{self.parser.get_relations_text(tables)}\n\n"
            f"样例数据：\n{self.parser.get_samples_text(tables)}"
        )
        raw = await self._run_agent(self.sql_generator_agent, input_text, "SQL 生成")
        sql = self.extract_sql(raw)
        self._log("sql_generate", f"生成 SQL：\n{sql}" if sql else "Agent 未生成有效 SQL")
        return sql

    # ------------------------- 4~5. SQL 校验与改写 -------------------------
    async def rewrite_sql(
        self,
        question: str,
        sql: str,
        error: str,
        tables: list[str],
    ) -> str:
        schema = "\n\n".join(self.parser.get_table_schema(t) for t in tables)
        input_text = (
            f"用户问题：\n{question}\n\n"
            f"选中的表结构：\n{schema}\n\n"
            f"外键关系：\n{self.parser.get_relations_text(tables)}\n\n"
            f"原始 SQL：\n{sql or '（空）'}\n\n"
            f"错误信息：\n{error.strip() or 'SQL 为空'}"
        )
        raw = await self._run_agent(self.sql_rewriter_agent, input_text, "SQL 改写")
        new_sql = self.extract_sql(raw)
        self._log("sql_rewrite", f"改写后的 SQL：\n{new_sql}" if new_sql else "Agent 未返回有效 SQL")
        return new_sql

    # ------------------------- 6. SQL 执行 -------------------------
    def execute_sql(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        columns, rows = self.parser.execute_sql(sql)
        self._log("sql_execute", f"返回 {len(rows)} 行，{len(columns)} 列：{columns}")
        return columns, rows

    # ------------------------- 7. 结果汇总 -------------------------
    async def summarize(
        self,
        question: str,
        sql: str,
        columns: list[str],
        rows: list[list[Any]],
    ) -> str:
        result_md = self._result_markdown(columns, rows)
        input_text = (
            f"用户问题：\n{question}\n\n"
            f"执行的 SQL：\n{sql}\n\n"
            f"查询结果（列名 + 数据）：\n{result_md}"
        )
        answer = (await self._run_agent(self.summary_agent, input_text, "结果汇总")).strip()
        self._log("result_summary", answer)
        return answer

    @staticmethod
    def _result_markdown(columns: list[str], rows: list[list[Any]], limit: int = 50) -> str:
        if not rows:
            return "（查询结果为空）"
        frame = pd.DataFrame(rows[:limit], columns=columns)
        markdown = frame.to_markdown(index=False)
        if len(rows) > limit:
            markdown += f"\n\n（仅展示前 {limit} 行，共 {len(rows)} 行）"
        return markdown

    # ------------------------- 工作流入口 -------------------------
    async def arun(self, question: str) -> dict[str, Any]:
        """异步执行完整 SQL Agent 工作流（适合 FastAPI / Notebook）。"""
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空。")
        self.trace = []
        self._log("question", question)

        # 2. 表选择
        tables = await self.select_tables(question)

        # 3. SQL 生成
        sql = await self.generate_sql(question, tables)

        # 4. 校验 -> 6. 执行；失败则 5. 改写后重试
        columns: list[str] = []
        rows: list[list[Any]] = []
        last_error = ""
        for attempt in range(1, self.max_retry + 1):
            if not sql:
                last_error = "模型没有生成有效的 SQL 语句。"
            else:
                ok, error = self.parser.check_sql(sql)
                if ok:
                    self._log("sql_check", f"第 {attempt} 次校验通过")
                    try:
                        columns, rows = self.execute_sql(sql)
                        last_error = ""
                        break
                    except Exception:
                        last_error = traceback.format_exc()
                        self._log("sql_execute", f"执行失败：{last_error}")
                else:
                    last_error = error
                    self._log("sql_check", f"第 {attempt} 次校验失败：{error.strip().splitlines()[-1]}")

            if attempt == self.max_retry:
                break
            sql = await self.rewrite_sql(question, sql, last_error, tables)

        if last_error:
            raise RuntimeError(
                f"SQL 未能通过校验/执行，已重试 {self.max_retry} 次。\n{last_error}"
            )

        # 7. 结果汇总
        answer = await self.summarize(question, sql, columns, rows)

        return {
            "question": question,
            "tables": tables,
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "answer": answer,
            "trace": self.trace,
        }

    def run(self, question: str) -> dict[str, Any]:
        """同步执行完整工作流。

        适合单次调用；同一进程内连续问多个问题时，建议在同一个事件循环里
        使用 `await agent.arun(...)`（CLI 已这样处理）。
        """
        return asyncio.run(self.arun(question))

    def close(self) -> None:
        self.parser.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
DEMO_QUESTIONS = [
    "数据库里一共有多少张专辑？",
    "销售额最高的前 5 位客户是谁？",
    "每个流派的曲目数量是多少？按数量从高到低排序。",
]


async def _run_one(agent: SQLAgent, question: str) -> None:
    print(f"\n{'=' * 60}\n问题：{question}\n{'=' * 60}")
    try:
        result = await agent.arun(question)
        print(f"\nSQL：{result['sql']}")
        print(f"\n=== 最终回答 ===\n{result['answer']}\n")
    except Exception as exc:
        print(f"查询失败：{exc}\n")


async def _run_cli(agent: SQLAgent, questions: list[str] | None) -> None:
    # 所有问题共用一个事件循环，避免 AsyncOpenAI 客户端跨事件循环复用出错
    if questions is not None:
        for question in questions:
            await _run_one(agent, question)
        return

    print("输入问题开始查询，输入 exit / quit 退出。")
    while True:
        try:
            question = input("\n问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in {"exit", "quit"}:
            break
        if not question:
            continue
        await _run_one(agent, question)


def main() -> None:
    cli = argparse.ArgumentParser(description="SQL Agent：自然语言 -> SQL -> 自然语言回答")
    cli.add_argument("question", nargs="*", help="要查询的问题（不传则进入交互模式）")
    cli.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 数据库路径")
    cli.add_argument("--model", default=None, help="模型名称，默认读取 .env 中的 OPENAI_MODEL")
    cli.add_argument("--max-retry", type=int, default=5, help="SQL 校验/改写最大轮次")
    cli.add_argument("--demo", action="store_true", help="运行内置示例问题")
    cli.add_argument("--quiet", action="store_true", help="不输出中间步骤")
    args = cli.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print(f"请先编辑 {ENV_PATH}，填入 OPENAI_API_KEY 后再运行。")
        print("  OPENAI_API_KEY=sk-xxx")
        return

    agent = SQLAgent(
        db_path=args.db,
        model=args.model,
        max_retry=args.max_retry,
        verbose=not args.quiet,
    )
    try:
        if args.demo:
            questions = DEMO_QUESTIONS
        elif args.question:
            questions = [" ".join(args.question)]
        else:
            questions = None
        asyncio.run(_run_cli(agent, questions))
    finally:
        agent.close()


if __name__ == "__main__":
    main()
