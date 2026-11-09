"""
SQL Agent（LangChain 版）—— 用 LangChain 复刻 week11 的 7 步 SQL agent workflow

流程（7 步）：
    1. 数据库解析   DBParser     —— 解析 schema、字段统计、外键关系、数据样例
    2. table 选择   select_tables —— 结合用户提问，选择需要使用的表（结构化输出）
    3. sql 生成     generate_sql —— 结合提问 + 选择的表 schema 生成 SQL
    4. sql 校验     validate_sql —— 用 DBParser.check_sql 检查 SQL 是否可执行
    5. sql 改写     rewrite_sql  —— SQL 执行/校验失败时，结合报错信息改写
    6. sql 执行     execute_sql  —— 真正执行 SQL，拿到结果
    7. 结果汇总     summarize    —— 将 SQL + 原始结果组织为自然语言回答

与 week11/sql_agent.py 的区别：
    - LLM 调用从裸 `openai.OpenAI` 换成 `langchain_openai.ChatOpenAI`（OpenAI 兼容协议，默认 DashScope/qwen）
    - 提示词用 `ChatPromptTemplate` 管理，输出用 `StrOutputParser` 解析
    - table 选择用 `PydanticOutputParser` 做结构化输出（JSON），并带正则兜底
    - 通过 LCEL（`prompt | llm | parser`）把每一步串成 chain

环境变量：
    export OPENAI_API_KEY=sk-xxx
    export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    export OPENAI_MODEL=qwen-plus   # 可选，默认 qwen-plus
"""

import os
import re
import json
import traceback
from typing import Union, List, Tuple, Optional, Dict, Any

from sqlalchemy import create_engine, inspect, func, select, Table, MetaData, text
import pandas as pd
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser


# --------------------------------------------------------------------------- #
# 1. 数据库解析（与 week11 保持一致，纯 SQLAlchemy 逻辑，不含 LLM）
# --------------------------------------------------------------------------- #
class DBParser:
    """DBParser：数据库的解析（schema / 字段统计 / 外键关系 / 数据样例）"""

    def __init__(self, db_url: str) -> None:
        """
        参数
            db_url: 数据库连接地址
                sqlite: sqlite:///./chinook.db
                mysql : mysql://root:123456@localhost:3306/mydb?charset=utf8mb4
        """
        self.db_type = 'sqlite' if 'sqlite' in db_url else (
            'mysql' if 'mysql' in db_url else 'unknown'
        )

        self.engine = create_engine(db_url, echo=False)
        self.db_url = db_url

        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()

        self._table_fields: Dict[str, Dict] = {}             # 每张表的字段信息（含统计）
        self.foreign_keys: List[Dict] = []                   # 外键关系
        self._table_sample: Dict[str, pd.DataFrame] = {}     # 每张表样例（首行）

        for table_name in self.table_names:
            print("Table ->", table_name)

            # 累计外键
            self.foreign_keys += [
                {
                    'constrained_table': table_name,
                    'constrained_columns': x['constrained_columns'],
                    'referred_table': x['referred_table'],
                    'referred_columns': x['referred_columns'],
                } for x in self.inspector.get_foreign_keys(table_name)
            ]

            # 字段元信息
            table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
            table_columns = self.inspector.get_columns(table_name)
            self._table_fields[table_name] = {x['name']: dict(x) for x in table_columns}

            with self.engine.connect() as conn:
                for column_meta in table_columns:
                    col_name = column_meta['name']
                    column_instance = getattr(table_instance.columns, col_name)
                    info = self._table_fields[table_name][col_name]

                    # distinct 个数
                    info['distinct'] = conn.execute(
                        select(func.count(func.distinct(column_instance)))
                    ).fetchone()[0]

                    # 众数（仅文本类字段）
                    field_type = str(info['type']).lower()
                    if 'text' in field_type or 'char' in field_type:
                        info['mode'] = conn.execute(
                            select(column_instance, func.count().label('cnt'))
                            .group_by(column_instance)
                            .order_by(func.count().desc())
                            .limit(1)
                        ).fetchone()[0]

                    # 缺失个数
                    info['nan_count'] = conn.execute(
                        select(func.count()).where(column_instance.is_(None))
                    ).fetchone()[0]

                    # max / min
                    info['max'] = conn.execute(select(func.max(column_instance))).fetchone()[0]
                    info['min'] = conn.execute(select(func.min(column_instance))).fetchone()[0]

                    # 任意取值（去重后取 3 个）
                    values = conn.execute(select(column_instance).limit(10)).all()
                    values = {str(x[0]) for x in values if x[0] is not None}
                    info['random'] = list(values)[:3]

            # 表样例（第一行）
            with self.engine.connect() as conn:
                row = conn.execute(select(table_instance)).fetchone()
            sample = pd.DataFrame([row]) if row is not None else pd.DataFrame()
            sample.columns = [x['name'] for x in table_columns]
            self._table_sample[table_name] = sample

    # -- 对外查询接口 ------------------------------------------------------ #
    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        """获取表字段信息（schema + 统计）"""
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        """获取数据库表之间的链接信息（外键）"""
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str) -> pd.DataFrame:
        """获取数据表样例"""
        return self._table_sample[table_name]

    def schema_overview(self) -> str:
        """生成全库 schema 概览（供 table 选择使用）"""
        lines = []
        for table_name in self.table_names:
            cols = list(self._table_fields[table_name].keys())
            lines.append(f"- {table_name}({', '.join(cols)})")
        rels = [
            f"{fk['constrained_table']}.{fk['constrained_columns'][0]} "
            f"-> {fk['referred_table']}.{fk['referred_columns'][0]}"
            for fk in self.foreign_keys
        ]
        if rels:
            lines.append("\n外键关系：\n" + "\n".join(f"- {r}" for r in rels))
        return "\n".join(lines)

    def check_sql(self, sql: str) -> Union[Tuple[bool, str], Tuple[bool, str]]:
        """检查 sql 是否可执行，返回 (是否成功, 报错信息)"""
        sql = (sql or "").strip()
        if not sql:
            return False, "SQL 为空，大模型未返回有效内容"
        try:
            with self.engine.begin() as conn:
                conn.execute(text(sql))
            return True, 'ok'
        except Exception:
            return False, traceback.format_exc()

    def execute_sql(self, sql: str) -> Tuple[List[str], List[Tuple]]:
        """执行 SQL，返回 (列名列表, 结果行列表)。不返回行的语句（DML 等）返回空结果。"""
        with self.engine.begin() as conn:
            result = conn.execute(text(sql))
            if result.returns_rows:
                columns = list(result.keys())
                rows = [tuple(r) for r in result]
            else:
                columns, rows = [], []
        return columns, rows


# --------------------------------------------------------------------------- #
# table 选择的结构化输出模型（供 PydanticOutputParser 使用）
# --------------------------------------------------------------------------- #
class TableSelection(BaseModel):
    """table 选择结果"""
    tables: List[str] = Field(description="需要查询的表名列表，如 ['albums']")
    reason: str = Field(description="选择这些表的理由")


# --------------------------------------------------------------------------- #
# 2~7. SQL Agent（LangChain 版）
# --------------------------------------------------------------------------- #
class LangChainSQLAgent:
    """用 LangChain 复刻的完整 SQL Agent：table 选择 → SQL 生成 → 校验 → 改写 → 执行 → 汇总"""

    def __init__(
        self,
        db_url: str = "sqlite:///./chinook.db",
        model: Optional[str] = None,
        retry_time: int = 5,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.parser = DBParser(db_url)

        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        base_url = base_url or os.environ.get(
            "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        if not api_key:
            raise RuntimeError(
                "缺少 OPENAI_API_KEY，请通过环境变量或参数传入（如 DashScope 的 API Key）。"
            )
        model = model or os.environ.get("OPENAI_MODEL") or "qwen-plus"

        # LangChain 的 OpenAI 兼容 Chat 模型：openai_api_base 指向 DashScope 兼容端点
        self.llm = ChatOpenAI(
            model=model,
            temperature=0.0,
            openai_api_key=api_key,
            openai_api_base=base_url,
        )
        self.model = model
        self.retry_time = retry_time

        # table 选择：结构化输出解析器（要求模型返回 JSON，并带正则兜底）
        self.table_parser = PydanticOutputParser(pydantic_object=TableSelection)

        # 各步骤的 prompt 模板（LCEL 用 {变量} 占位）
        self.select_prompt = ChatPromptTemplate.from_messages([
            ("system",
             "你是一个专业的 SQL 专家。请分析用户提问，判断需要查询数据库中的哪一张或哪几张表，"
             "并说明理由。"),
            ("human",
             "数据库所有表及字段如下：\n{schema}\n\n"
             "用户提问：{question}\n\n"
             "{format_instructions}")
        ])
        self.generate_prompt = ChatPromptTemplate.from_messages([
            ("system",
             "你是一个专业的 SQL 专家，请根据用户提问生成对应的 SQL 语句。"
             "只输出 SQL，不要输出其他内容，不要加注释。"),
            ("human",
             "涉及的表：{tables}\n\n"
             "表 schema（含字段统计信息）：\n{schema}\n\n"
             "表样例数据：\n{sample}\n\n"
             "用户提问：{question}\n")
        ])
        self.rewrite_prompt = ChatPromptTemplate.from_messages([
            ("system",
             "你是一个专业的 SQL 专家。下面这条 SQL 执行时报错，请根据错误信息和用户提问，"
             "修正并重新生成正确的 SQL。只输出 SQL，不要输出其他内容。"),
            ("human",
             "用户提问：{question}\n\n"
             "原始 SQL：\n{sql}\n\n"
             "错误信息：\n{error}\n")
        ])
        self.summarize_prompt = ChatPromptTemplate.from_messages([
            ("system",
             "你是答案汇总专家，请将下面的问题回答组织为简洁、准确的自然语言，"
             "用中文回答，直接给出结论即可。"),
            ("human",
             "原始问题：{question}\n\n"
             "执行 SQL：{sql}\n\n"
             "原始结果（列：{columns}）：\n{result}\n")
        ])

    # -- 大模型调用（统一入口，异常兜底） --------------------------------- #
    def _invoke(self, prompt: ChatPromptTemplate, variables: Dict[str, Any],
                temperature: float = 0.0) -> str:
        """用 LCEL 串起 prompt | llm | StrOutputParser，返回字符串。异常时返回空串。"""
        # 生成/改写/汇总需要较低的采样温度；temperature 只在需要时覆盖
        llm = self.llm if temperature == 0.0 else self.llm.bind(temperature=temperature)
        chain = prompt | llm | StrOutputParser()
        try:
            return chain.invoke(variables)
        except Exception as e:
            print(f"调用大模型失败：{type(e).__name__}: {e}")
            return ""

    # -- 2. table 选择 ----------------------------------------------------- #
    def select_tables(self, question: str) -> List[str]:
        """结合用户提问，从可用表中选择需要使用的表（结构化输出）"""
        variables = {
            "schema": self.parser.schema_overview(),
            "question": question,
            "format_instructions": self.table_parser.get_format_instructions(),
        }
        content = self._invoke(self.select_prompt, variables)
        tables, _ = self._parse_tables(content)

        # 兜底：过滤掉不存在的表
        tables = [t for t in tables if t in self.parser.table_names]
        if not tables:
            tables = [self.parser.table_names[0]]
        return tables

    def _parse_tables(self, content: str) -> Tuple[List[str], str]:
        """用 PydanticOutputParser 解析 {tables, reason}，失败时正则兜底"""
        if content:
            try:
                data = self.table_parser.parse(content)
                if isinstance(data, TableSelection):
                    return [str(t) for t in data.tables], str(data.reason)
            except Exception:
                pass
        # 正则兜底：从 LLM 输出中提取 JSON
        data = self._extract_json(content)
        if isinstance(data, dict):
            tables = data.get("tables") or []
            reason = data.get("reason") or ""
            if isinstance(tables, list):
                return [str(t) for t in tables], str(reason)
        return [], ""

    # -- 3. sql 生成 ------------------------------------------------------- #
    def generate_sql(self, question: str, table_names: List[str]) -> str:
        """结合提问 + 所选表 schema 生成 SQL"""
        schema_md, sample_md = self._tables_context(table_names)
        variables = {
            "tables": ", ".join(table_names),
            "schema": schema_md,
            "sample": sample_md,
            "question": question,
        }
        content = self._invoke(self.generate_prompt, variables)
        return self._extract_sql(content)

    # -- 4. sql 校验 ------------------------------------------------------- #
    def validate_sql(self, sql: str) -> Tuple[bool, str]:
        """检查 SQL 是否合理/可执行"""
        return self.parser.check_sql(sql)

    # -- 5. sql 改写 ------------------------------------------------------- #
    def rewrite_sql(self, question: str, sql: str, error: str) -> str:
        """SQL 校验/执行失败时，结合报错信息改写"""
        variables = {"question": question, "sql": sql, "error": error}
        content = self._invoke(self.rewrite_prompt, variables)
        return self._extract_sql(content)

    # -- 6. sql 执行 ------------------------------------------------------- #
    def execute_sql(self, sql: str) -> Tuple[List[str], List[Tuple]]:
        """执行 SQL，返回 (列名, 行列表)"""
        return self.parser.execute_sql(sql)

    # -- 7. 结果汇总 ------------------------------------------------------- #
    def summarize(self, question: str, sql: str, columns: List[str], rows: List[Tuple]) -> str:
        """将 SQL + 原始结果汇总为自然语言回答"""
        variables = {
            "question": question,
            "sql": sql,
            "columns": columns,
            "result": self._format_result(columns, rows),
        }
        return self._invoke(self.summarize_prompt, variables, temperature=0.3)

    # -- 总入口 ------------------------------------------------------------ #
    def run(self, question: str) -> Dict[str, Any]:
        """执行完整 workflow，返回过程 trace 与最终结果"""
        trace: Dict[str, Any] = {"question": question}

        # 2. table 选择
        tables = self.select_tables(question)
        trace["tables"] = tables

        # 3. sql 生成
        sql = self.generate_sql(question, tables)
        trace["sql_initial"] = sql

        # 4/5/6. 校验 -> 执行 -> 失败改写（重试）
        columns, rows = [], []
        final_sql = sql
        for attempt in range(self.retry_time):
            ok, err = self.validate_sql(final_sql)
            if ok:
                try:
                    columns, rows = self.execute_sql(final_sql)
                    trace["sql_final"] = final_sql
                    trace["attempts"] = attempt + 1
                    break
                except Exception:
                    err = traceback.format_exc()
            if attempt == self.retry_time - 1:
                trace["error"] = err
                trace["attempts"] = attempt + 1
                break
            # 改写
            final_sql = self.rewrite_sql(question, final_sql, err)
            trace[f"sql_rewrite_{attempt + 1}"] = final_sql

        trace["result_columns"] = columns
        trace["result_rows"] = rows

        # 7. 结果汇总
        if columns:
            trace["answer"] = self.summarize(question, final_sql, columns, rows)
        elif not final_sql.strip():
            trace["answer"] = (
                "大模型未能生成有效 SQL。请检查 OPENAI_API_KEY / OPENAI_BASE_URL "
                "是否配置正确、模型名是否匹配、账号是否还有额度。"
            )
        else:
            trace["answer"] = f"未能成功执行 SQL，错误信息：{trace.get('error', '未知错误')}"
        return trace

    # -- 工具方法 ---------------------------------------------------------- #
    def _tables_context(self, table_names: List[str]) -> Tuple[str, str]:
        """生成所选表的 schema 与样例 markdown"""
        schema_parts, sample_parts = [], []
        for t in table_names:
            schema_parts.append(
                f"### {t}\n{self._df_to_markdown(self.parser.get_table_fields(t))}"
            )
            sample_parts.append(
                f"### {t}\n{self._df_to_markdown(self.parser.get_table_sample(t))}"
            )
        return "\n\n".join(schema_parts), "\n\n".join(sample_parts)

    @staticmethod
    def _df_to_markdown(df: pd.DataFrame) -> str:
        """将 DataFrame 转为 markdown 表格（不依赖 tabulate）"""

        def fmt(v) -> str:
            if v is None:
                return ""
            if isinstance(v, float) and pd.isna(v):
                return ""
            if isinstance(v, (list, tuple, set)):
                return ", ".join(str(x) for x in v)
            return str(v)

        data = df.reset_index()
        headers = [str(c) for c in data.columns]
        rows = [[fmt(v) for v in row] for row in data.itertuples(index=False, name=None)]

        def render_line(cells: List[str]) -> str:
            return "| " + " | ".join(cells) + " |"

        lines = [render_line(headers), render_line(["---"] * len(headers))]
        lines += [render_line(r) for r in rows]
        return "\n".join(lines)

    def _extract_json(self, content: str) -> Any:
        """从 LLM 输出中提取 JSON（兼容 markdown 代码块）"""
        if not content:
            return None
        content = re.sub(r"```(?:json)?", "", content).strip()
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return None

    def _extract_sql(self, content: str) -> str:
        """从 LLM 输出中提取 SQL（兼容 markdown 代码块）"""
        if not content:
            return ""
        m = re.search(r"```(?:sql)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return content.strip().strip("`").strip()

    @staticmethod
    def _format_result(columns: List[str], rows: List[Tuple], limit: int = 20) -> str:
        """将结果格式化为可读字符串"""
        if not rows:
            return "（空结果，0 行）"
        lines = ["\t".join(columns)]
        for r in rows[:limit]:
            lines.append("\t".join(str(x) for x in r))
        if len(rows) > limit:
            lines.append(f"... 共 {len(rows)} 行，仅展示前 {limit} 行")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 演示
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    agent = LangChainSQLAgent()

    demo_questions = [
        "albums 表里一共有多少张专辑？",
        "一共有多少个不同的艺术家？",
        "每位客户（Customer）的消费总额是多少？列出前 5 名。",
        "每个流派（Genre）下有多少首歌曲（Track）？",
    ]

    for q in demo_questions:
        print("\n" + "=" * 70)
        print("用户提问：", q)
        trace = agent.run(q)
        print("选择表   ：", trace["tables"])
        print("生成 SQL ：", trace.get("sql_initial"))
        if trace.get("result_rows") or trace.get("result_columns"):
            print("结果行数 ：", len(trace["result_rows"]))
        print("最终回答 ：", trace["answer"])
