# -*- coding: utf-8 -*-
"""
Week12 作业1 · 单文件版 SQL Agent（基于 chinook.db）

完整链路（七个阶段）：
    1. 数据库解析  -> 2. 表选择 -> 3. SQL 生成 -> 4. SQL 校验
    -> 5. SQL 改写（失败时）-> 6. SQL 执行 -> 7. 结果汇总

运行方式：
    python sql_agent_all.py --db           只做数据库解析，不需要 API Key
    python sql_agent_all.py -q "一共有多少首歌？"   回答单个问题
    python sql_agent_all.py                依次跑内置测试用例

使用前请在下方【配置区】填入 LLM_API_KEY。
参考：week11-ChatBI数据智能问答/Week12/04_SQL-Code-Agent-Demo/sql-agent.ipynb
"""
import argparse
import json
import os
import re
import sys
import time
import traceback
from typing import Dict, List, Optional, Tuple

import pandas as pd
from openai import OpenAI
from sqlalchemy import create_engine, func, inspect, MetaData, select, Table, text

# ======================================================================
# 配置区
# ======================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "chinook.db")

# Windows 路径统一转成正斜杠，避免 SQLite URL 解析问题
DB_URL = "sqlite:///" + DB_PATH.replace("\\", "/")

# ★★★ 在这里填入你的 API Key（也可以设置环境变量 LLM_API_KEY）★★★
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# 默认按 DeepSeek（OpenAI 兼容模式）；换服务商改这两项即可
# 阿里云百炼示例：https://dashscope.aliyuncs.com/compatible-mode/v1 + qwen-max
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

LLM_TIMEOUT = 60          # 单次请求超时（秒）
LLM_MAX_RETRY = 3         # 网络异常重试次数

MAX_TABLES_FOR_LLM = 3    # 表选择阶段最多选几张表
MAX_REWRITE_RETRY = 3     # SQL 改写阶段最多重试几次
MAX_RESULT_ROWS = 50      # 执行结果最多返回多少行
MAX_SUMMARY_ROWS = 20     # 结果汇总时最多展示多少行给 LLM

# 安全黑名单：出现即拒绝执行（防止 LLM 生成破坏性语句）
FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "replace", "attach", "detach", "pragma", "vacuum", "grant", "revoke",
    "begin", "commit", "rollback",
}


# ======================================================================
# 提示词模板
# ======================================================================

TABLE_SELECTION_PROMPT = """你是一个专业的数据库专家。用户会提出一个自然语言问题，
你需要判断应该查询数据库中哪些表。

数据库全部表及字段：
{all_tables}

表之间的外键关系：
{relations}

用户问题：{question}

要求：
1. 只从上面列出的表中选择，最多选 {max_tables} 张
2. 如果问题与数据库内容完全无关，返回空列表
3. 只输出 JSON 数组，不要输出任何解释，例如：["tracks", "albums"]
"""

SQL_GENERATION_PROMPT = """你是一个专业的数据库专家，请根据用户问题生成 SQL 查询语句。

相关表结构：
{schema}

表之间的外键关系：
{relations}

数据样例：
{samples}

用户问题：{question}

要求：
1. 只能生成 SELECT 查询，禁止任何修改数据的语句
2. 表名和字段名必须严格使用上面给出的真实名称
3. 若结果可能很多行，使用 ORDER BY 明确排序
4. 只输出 SQL 语句，不要输出解释，不要加 ```sql 代码块标记
"""

SQL_REWRITE_PROMPT = """你是一个专业的数据库专家。下面这条 SQL 执行失败了，请修正它。

相关表结构：
{schema}

原始问题：{question}

有问题的 SQL：
{sql}

错误信息：
{error}

要求：
1. 根据错误信息修正 SQL，只能生成 SELECT 查询
2. 只输出修正后的 SQL，不要输出解释，不要加 ```sql 代码块标记
"""

RESULT_SUMMARY_PROMPT = """你是一个专业的数据分析师。请根据 SQL 查询结果，用自然语言回答用户问题。

用户问题：{question}

执行的 SQL：
{sql}

查询结果（最多展示 {max_rows} 行）：
{result}

要求：
1. 直接回答用户问题，语言简洁准确
2. 如果查询结果为空，直接说明没有查到符合条件的数据，不要编造
3. 如果结果被截断，需要提示用户「仅展示了部分数据」
4. 不要输出 SQL 语句本身，也不要解释你的分析过程
"""

CANNOT_ANSWER_PROMPT = """用户问题：{question}

当前数据库包含的表：
{all_tables}

这个问题无法通过上述数据表回答。请用一句自然、礼貌的话告知用户，
并简要说明数据库里有哪些类型的数据可以查询。不要编造数据。
"""


# ======================================================================
# 阶段1：数据库解析
# ======================================================================

class DBParser:
    """数据库解析器：扫描表结构、字段统计、外键关系、样例数据。

    参考 sql-agent.ipynb 的 DBParser 重写，主要差异：
    1. 不使用 SQLAlchemy 2.0 已移除的 engine.execute()，统一用 conn.execute()
    2. 不使用 Select.filter()，统一用 .where()
    3. 空值判断用 .is_(None) 而不是 == None
    """

    def __init__(self, db_url: str) -> None:
        """初始化并完成全库扫描。

        db_url: sqlite 相对路径 -> sqlite:///./chinook.db
                sqlite 绝对路径 -> sqlite:////abs/path/chinook.db
                mysql           -> mysql+pymysql://root:123456@localhost:3306/mydb
        """
        if "sqlite" in db_url:
            self.db_type = "sqlite"
        elif "mysql" in db_url:
            self.db_type = "mysql"
        else:
            self.db_type = "unknown"

        self.engine = create_engine(db_url, echo=False)
        self.conn = self.engine.connect()
        self.db_url = db_url

        self.inspector = inspect(self.engine)
        self.table_names: List[str] = self.inspector.get_table_names()

        self._table_fields: Dict[str, Dict] = {}          # 每张表的字段统计
        self.foreign_keys: List[Dict] = []                 # 全库外键关系
        self._table_sample: Dict[str, pd.DataFrame] = {}   # 每张表的样例数据

        self._scan()

    # ------------------------------------------------------------------
    # 内部：逐表扫描
    # ------------------------------------------------------------------
    def _scan(self) -> None:
        """遍历所有表，收集字段统计、外键、样例。"""
        for table_name in self.table_names:
            # sqlite_ 开头的系统表不参与分析
            if table_name.startswith("sqlite_"):
                continue

            # 收集外键
            for fk in self.inspector.get_foreign_keys(table_name):
                self.foreign_keys.append({
                    "constrained_table": table_name,
                    "constrained_columns": fk.get("constrained_columns"),
                    "referred_table": fk.get("referred_table"),
                    "referred_columns": fk.get("referred_columns"),
                })

            table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
            table_columns = self.inspector.get_columns(table_name)
            self._table_fields[table_name] = {c["name"]: c for c in table_columns}

            for column_meta in table_columns:
                column = getattr(table_instance.columns, column_meta["name"])
                stats = self._column_stats(column, column_meta)
                self._table_fields[table_name][column_meta["name"]].update(stats)

            self._table_sample[table_name] = self._read_sample(table_instance, table_columns)

    def _column_stats(self, column, column_meta: Dict) -> Dict:
        """统计单个字段：distinct / 众数 / 空值数 / max / min / 若干取值。"""
        stats: Dict = {}

        # 去重值个数
        stats["distinct"] = self.conn.execute(
            select(func.count(func.distinct(column)))
        ).fetchone()[0]

        # 众数（只对文本类字段统计）
        field_type = str(column_meta["type"]).lower()
        if "text" in field_type or "char" in field_type:
            top1 = self.conn.execute(
                select(column, func.count().label("count"))
                .group_by(column)
                .order_by(func.count().desc())
                .limit(1)
            ).fetchone()
            stats["mode"] = top1[0] if top1 else None

        # 空值数量（用 is_(None)，不要用 == None）
        stats["nan_count"] = self.conn.execute(
            select(func.count()).where(column.is_(None))
        ).fetchone()[0]

        # 最大 / 最小
        stats["max"] = self.conn.execute(select(func.max(column))).fetchone()[0]
        stats["min"] = self.conn.execute(select(func.min(column))).fetchone()[0]

        # 若干取值（取前 10 个，去重后保留 3 个）
        rows = self.conn.execute(select(column).limit(10)).fetchall()
        values = [str(r[0]) for r in rows if r[0] is not None]
        stats["random"] = list(dict.fromkeys(values))[:3]

        return stats

    def _read_sample(self, table_instance, table_columns: List[Dict]) -> pd.DataFrame:
        """读取表的前若干行作为样例；空表也要保证列名正确。"""
        rows = self.conn.execute(select(table_instance).limit(3)).fetchall()
        df = pd.DataFrame(rows)
        if df.empty:
            df = pd.DataFrame(columns=[c["name"] for c in table_columns])
        else:
            df.columns = [c["name"] for c in table_columns]
        return df

    # ------------------------------------------------------------------
    # 对外：供 Agent 使用的查询接口
    # ------------------------------------------------------------------
    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        """获取某张表的字段信息（DataFrame）。"""
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        """获取全库外键关系（DataFrame）。"""
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str) -> pd.DataFrame:
        """获取某张表的样例数据（DataFrame）。"""
        return self._table_sample[table_name]

    def get_schema_text(self, table_name: str) -> str:
        """把一张表的结构压成一段紧凑文本，用于喂给 LLM。"""
        fields = self.get_table_fields(table_name)
        lines = [f"表名：{table_name}"]
        for field_name, row in fields.iterrows():
            parts = [f"  - {field_name} ({row['type']})"]
            if pd.notna(row.get("distinct")):
                parts.append(f"去重值={row['distinct']}")
            if pd.notna(row.get("max")):
                parts.append(f"max={row['max']}")
            if pd.notna(row.get("min")):
                parts.append(f"min={row['min']}")
            if pd.notna(row.get("nan_count")) and row.get("nan_count"):
                parts.append(f"空值={row['nan_count']}")
            if row.get("random"):
                parts.append(f"示例={list(row['random'])[:3]}")
            lines.append("，".join(parts))
        return "\n".join(lines)

    def get_sample_text(self, table_name: str) -> str:
        """把表样例压成文本（to_string 不依赖 tabulate 库）。"""
        df = self._table_sample[table_name]
        if df.empty:
            return f"表 {table_name} 为空表"
        return df.to_string(index=False)

    def get_all_table_summary(self) -> str:
        """生成全库的表清单摘要，用于表选择阶段。"""
        blocks = []
        for table_name in self.table_names:
            if table_name.startswith("sqlite_"):
                continue
            columns = list(self._table_fields[table_name].keys())
            blocks.append(f"表 {table_name}，字段：{', '.join(columns)}")
        return "\n".join(blocks)

    # ------------------------------------------------------------------
    # SQL 校验与执行
    # ------------------------------------------------------------------
    def check_sql(self, sql: str) -> Tuple[bool, str]:
        """用 EXPLAIN 试跑 SQL，验证语法和字段是否正确（不真正取数据）。"""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("EXPLAIN " + sql))
            return True, "ok"
        except Exception:
            return False, traceback.format_exc(limit=2)

    def execute_sql(self, sql: str) -> Tuple[pd.DataFrame, str]:
        """执行 SQL 并返回 DataFrame。返回 (结果, 错误信息)。"""
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql))
                rows = result.fetchall()
                columns = list(result.keys())
            return pd.DataFrame(rows, columns=columns), ""
        except Exception:
            return pd.DataFrame(), traceback.format_exc(limit=2)

    def close(self) -> None:
        """关闭数据库连接。"""
        self.conn.close()
        self.engine.dispose()


# ======================================================================
# LLM 客户端
# ======================================================================

class LLMClient:
    """极简 LLM 客户端：OpenAI 兼容协议，带 Key 校验和重试。"""

    def __init__(self) -> None:
        if not LLM_API_KEY:
            raise ValueError(
                "未配置 LLM API Key。请在文件顶部 LLM_API_KEY 填入，"
                "或设置环境变量 LLM_API_KEY。"
            )
        self.client = OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            timeout=LLM_TIMEOUT,
        )
        self.model = LLM_MODEL

    def chat(self, prompt: str, system: Optional[str] = None) -> str:
        """发送一次对话，返回纯文本。网络异常时按指数退避重试。"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_error = None
        for attempt in range(LLM_MAX_RETRY):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0,
                )
                return (response.choices[0].message.content or "").strip()
            except Exception as e:
                last_error = e
                # 认证类错误重试也没用，直接抛出
                if "401" in str(e) or "Authentication" in type(e).__name__:
                    raise
                if attempt < LLM_MAX_RETRY - 1:
                    time.sleep(2 ** attempt)

        raise RuntimeError(f"LLM 调用失败，已重试 {LLM_MAX_RETRY} 次：{last_error}")


# ======================================================================
# SQL Agent：七阶段编排
# ======================================================================

class SQLAgent:
    """基于 LLM 的数据库问答 Agent。"""

    def __init__(self, db_url: str = None, llm: LLMClient = None) -> None:
        self.db_url = db_url or DB_URL
        self.llm = llm or LLMClient()
        self.parser: Optional[DBParser] = None
        self.trace: List[Dict] = []   # 记录每一步的执行情况，便于学习和排查

    # ------------------------------------------------------------------
    # 阶段1：数据库解析
    # ------------------------------------------------------------------
    def parse_database(self) -> DBParser:
        start = time.time()
        self.parser = DBParser(self.db_url)
        tables = [t for t in self.parser.table_names if not t.startswith("sqlite_")]
        self._log("1-数据库解析", f"共 {len(tables)} 张表：{tables}", start)
        return self.parser

    # ------------------------------------------------------------------
    # 阶段2：表选择
    # ------------------------------------------------------------------
    def select_tables(self, question: str) -> List[str]:
        start = time.time()
        prompt = TABLE_SELECTION_PROMPT.format(
            all_tables=self.parser.get_all_table_summary(),
            relations=self._relations_text() or "无外键关系",
            question=question,
            max_tables=MAX_TABLES_FOR_LLM,
        )
        raw = self.llm.chat(prompt)
        tables = self._parse_table_list(raw)

        # 过滤掉模型幻觉出来的、库里并不存在的表
        valid = [t for t in tables if t in self.parser.table_names][:MAX_TABLES_FOR_LLM]
        self._log("2-表选择", f"模型返回 {tables}，有效 {valid}", start)
        return valid

    # ------------------------------------------------------------------
    # 阶段3：SQL 生成
    # ------------------------------------------------------------------
    def generate_sql(self, question: str, tables: List[str]) -> str:
        start = time.time()
        prompt = SQL_GENERATION_PROMPT.format(
            schema=self._schema_text(tables),
            relations=self._relations_text(tables) or "无外键关系",
            samples=self._samples_text(tables),
            question=question,
        )
        sql = self._clean_sql(self.llm.chat(prompt))
        self._log("3-SQL生成", sql.replace("\n", " "), start)
        return sql

    # ------------------------------------------------------------------
    # 阶段4：SQL 校验（安全检查 + 语法检查）
    # ------------------------------------------------------------------
    def validate_sql(self, sql: str) -> Tuple[bool, str]:
        start = time.time()

        # 4.1 安全检查：只允许单条 SELECT
        safe, reason = self._security_check(sql)
        if not safe:
            self._log("4-SQL校验", f"安全校验未通过：{reason}", start)
            return False, reason

        # 4.2 语法检查：用 EXPLAIN 试跑，不真正取数据
        passed, error = self.parser.check_sql(sql)
        if not passed:
            self._log("4-SQL校验", "语法校验未通过", start)
            return False, error

        self._log("4-SQL校验", "通过", start)
        return True, "ok"

    # ------------------------------------------------------------------
    # 阶段5：SQL 改写（依据错误信息修正）
    # ------------------------------------------------------------------
    def rewrite_sql(self, question: str, sql: str, error: str, tables: List[str]) -> str:
        start = time.time()
        prompt = SQL_REWRITE_PROMPT.format(
            schema=self._schema_text(tables),
            question=question,
            sql=sql,
            error=error,
        )
        new_sql = self._clean_sql(self.llm.chat(prompt))
        self._log("5-SQL改写", new_sql.replace("\n", " "), start)
        return new_sql

    # ------------------------------------------------------------------
    # 阶段6：SQL 执行
    # ------------------------------------------------------------------
    def execute_sql(self, sql: str) -> Tuple[pd.DataFrame, str, bool]:
        """返回 (结果, 错误信息, 是否补了LIMIT)。"""
        start = time.time()

        # 没有 LIMIT 时自动补一个，防止全表拉取
        truncated = False
        limited_sql = sql
        if " limit " not in (" " + sql.lower() + " "):
            limited_sql = sql.rstrip().rstrip(";") + f" LIMIT {MAX_RESULT_ROWS}"
            truncated = True

        df, error = self.parser.execute_sql(limited_sql)
        if error:
            self._log("6-SQL执行", "执行失败", start)
            return df, error, truncated

        self._log("6-SQL执行", f"返回 {len(df)} 行", start)
        return df, "", truncated

    # ------------------------------------------------------------------
    # 阶段7：结果汇总
    # ------------------------------------------------------------------
    def summarize(self, question: str, sql: str, df: pd.DataFrame, truncated: bool) -> str:
        start = time.time()

        if df.empty:
            result_text = "（查询结果为空，没有任何行）"
        else:
            result_text = df.head(MAX_SUMMARY_ROWS).to_string(index=False)
            if len(df) > MAX_SUMMARY_ROWS:
                result_text += f"\n（共 {len(df)} 行，仅展示前 {MAX_SUMMARY_ROWS} 行）"
            if truncated:
                result_text += f"\n（SQL 已自动追加 LIMIT {MAX_RESULT_ROWS}）"

        prompt = RESULT_SUMMARY_PROMPT.format(
            question=question,
            sql=sql,
            result=result_text,
            max_rows=MAX_SUMMARY_ROWS,
        )
        answer = self.llm.chat(prompt)
        self._log("7-结果汇总", "完成", start)
        return answer

    # ------------------------------------------------------------------
    # 总编排
    # ------------------------------------------------------------------
    def ask(self, question: str) -> Dict:
        """完整跑一遍七个阶段，返回结构化结果。"""
        self.trace = []
        result = {
            "question": question,
            "tables": [],
            "sql": "",
            "rewrite_times": 0,
            "rows": 0,
            "answer": "",
            "success": False,
            "trace": self.trace,
        }

        # 阶段1：数据库解析（只需一次，已解析则跳过）
        if self.parser is None:
            self.parse_database()

        # 阶段2：表选择
        tables = self.select_tables(question)
        result["tables"] = tables

        # 选不出表 -> 该问题无法用数据库回答，走兜底
        if not tables:
            result["answer"] = self.llm.chat(CANNOT_ANSWER_PROMPT.format(
                question=question,
                all_tables=self.parser.get_all_table_summary(),
            ))
            return result

        # 阶段3：SQL 生成
        sql = self.generate_sql(question, tables)
        result["sql"] = sql

        # 阶段4 + 5：校验，失败就改写重试
        passed, error = self.validate_sql(sql)
        rewrite_times = 0
        while not passed and rewrite_times < MAX_REWRITE_RETRY:
            rewrite_times += 1
            sql = self.rewrite_sql(question, sql, error, tables)
            result["sql"] = sql
            passed, error = self.validate_sql(sql)
        result["rewrite_times"] = rewrite_times

        if not passed:
            result["answer"] = (
                f"SQL 校验始终未通过，已重试 {rewrite_times} 次。"
                f"最后一次错误：{error}"
            )
            return result

        # 阶段6：执行
        df, exec_error, truncated = self.execute_sql(sql)
        if exec_error:
            result["answer"] = f"SQL 执行失败：{exec_error}"
            return result
        result["rows"] = len(df)

        # 阶段7：汇总
        result["answer"] = self.summarize(question, sql, df, truncated)
        result["success"] = True
        return result

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _log(self, stage: str, message: str, start: float) -> None:
        cost = round(time.time() - start, 3)
        self.trace.append({"stage": stage, "message": message, "seconds": cost})
        print(f"[{stage}] {message}（{cost}s）")

    def _schema_text(self, tables: List[str]) -> str:
        return "\n\n".join(self.parser.get_schema_text(t) for t in tables)

    def _samples_text(self, tables: List[str]) -> str:
        return "\n\n".join(
            f"表 {t} 样例：\n{self.parser.get_sample_text(t)}" for t in tables
        )

    def _relations_text(self, tables: List[str] = None) -> str:
        """外键关系文本；传 tables 时只保留涉及这些表的关系。"""
        rows = self.parser.foreign_keys
        if not rows:
            return ""
        if tables:
            rows = [
                r for r in rows
                if r["constrained_table"] in tables and r["referred_table"] in tables
            ]
        return "\n".join(
            f"{r['constrained_table']}.{r['constrained_columns']} -> "
            f"{r['referred_table']}.{r['referred_columns']}"
            for r in rows
        )

    @staticmethod
    def _parse_table_list(raw: str) -> List[str]:
        """从模型输出里解析表名列表，兼容 JSON 和各种包裹格式。"""
        match = re.search(r"\[.*?\]", raw, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, list):
                    return [str(x).strip() for x in data]
            except json.JSONDecodeError:
                pass
        # 兜底：按逗号/换行切分
        cleaned = raw.replace("[", "").replace("]", "").replace('"', "")
        return [x.strip() for x in re.split(r"[,\n，]", cleaned) if x.strip()]

    @staticmethod
    def _clean_sql(sql: str) -> str:
        """剥离模型输出里的 ```sql 代码块包裹和多余说明。"""
        sql = sql.strip()
        sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.I)
        sql = re.sub(r"\s*```$", "", sql)
        return sql.strip()

    @staticmethod
    def _security_check(sql: str) -> Tuple[bool, str]:
        """安全检查：只允许单条 SELECT / WITH 查询。"""
        if not sql.strip():
            return False, "SQL 为空"

        # 去掉注释后再判断
        no_comment = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
        no_comment = re.sub(r"--[^\n]*", " ", no_comment)

        # 去掉字符串字面量，避免把 'update' 这类数据误判为关键字
        no_string = re.sub(r"'([^']|'')*'", "''", no_comment)
        cleaned = no_string.strip().rstrip(";").strip()

        # 多条语句（中间出现分号）直接拒绝
        if ";" in cleaned:
            return False, "不允许一次执行多条 SQL"

        first_word = cleaned.split()[0].lower() if cleaned.split() else ""
        if first_word not in ("select", "with"):
            return False, f"只允许 SELECT 查询，当前语句以 {first_word} 开头"

        for keyword in FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{keyword}\b", cleaned.lower()):
                return False, f"SQL 中包含被禁用的关键字：{keyword}"

        return True, "ok"


# ======================================================================
# 入口
# ======================================================================

# 内置测试用例：覆盖一般场景和边界场景
TEST_CASES = [
    ("T1", "一共有多少首歌？"),
    ("T2", "销售额最高的前 5 个国家是哪些？"),
    ("T3", "每个流派的歌曲数量分别是多少？"),
    ("T4", "哪位员工的工龄最长？"),
    ("B1", "今天天气怎么样？"),
]


def print_result(result: dict) -> None:
    """格式化打印一次 Agent 的完整输出。"""
    print("\n" + "=" * 70)
    print("问题：", result["question"])
    print("-" * 70)
    print("选中表：", result["tables"])
    print("生成SQL：", result["sql"])
    print("改写次数：", result["rewrite_times"])
    print("结果行数：", result["rows"])
    print("-" * 70)
    print("回答：", result["answer"])
    print("=" * 70)


def main() -> None:
    arg_parser = argparse.ArgumentParser(description="基于 LLM 的 SQL Agent")
    arg_parser.add_argument("--db", action="store_true", help="只执行数据库解析阶段")
    arg_parser.add_argument("-q", "--question", type=str, help="要提问的自然语言问题")
    args = arg_parser.parse_args()

    # 模式一：只跑数据库解析（不依赖 LLM）
    if args.db:
        print("数据库：", DB_URL)
        db = DBParser(DB_URL)
        tables = [t for t in db.table_names if not t.startswith("sqlite_")]
        print(f"\n共解析到 {len(tables)} 张表：")
        for t in tables:
            print(f"\n--- {t} ---")
            print(db.get_schema_text(t))
        print("\n外键关系：")
        relations = db.get_data_relations()
        print(relations.to_string(index=False) if not relations.empty else "无")
        db.close()
        return

    # 检查 Key 是否已配置
    if not LLM_API_KEY:
        print("未配置 LLM_API_KEY！请在文件顶部填入 API Key，"
              "或设置环境变量 LLM_API_KEY。")
        print("提示：可以先运行  python sql_agent_all.py --db  验证数据库解析阶段。")
        sys.exit(1)

    agent = SQLAgent()
    agent.parse_database()   # 阶段1 只跑一次，后续复用

    # 模式二：回答单个问题
    if args.question:
        print_result(agent.ask(args.question))
        return

    # 模式三：跑内置测试用例
    for case_id, question in TEST_CASES:
        print(f"\n\n########## 测试用例 {case_id} ##########")
        try:
            print_result(agent.ask(question))
        except Exception as e:
            print(f"用例 {case_id} 执行异常：{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
