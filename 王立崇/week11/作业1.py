# -*- coding: utf-8 -*-
"""
完整的 SQL Agent workflow（ChatBI 数据智能问答）

流程：
    1. 数据库解析  -> DBParser 读取表结构 / 字段 / 外键 / 样例
    2. table 选择  -> 根据用户问题，用大模型从候选表里挑出相关表
    3. SQL 生成    -> 大模型结合所选表结构生成 SQL
    4. SQL 校验    -> 在数据库上预执行，判断 SQL 是否合法
    5. SQL 改写    -> 校验失败时，把报错信息喂给大模型重新生成
    6. SQL 执行    -> 在数据库上正式执行，返回结果
    7. 结果汇总    -> 大模型把「问题 + SQL + 结果」组织成自然语言回答

依赖：sqlalchemy / pandas / requests / tabulate / numpy（见 requirements.txt）

用法：
    from sql_agent import SQLAgent
    agent = SQLAgent(db_url="sqlite:///./chinook.db", api_key="sk-xxx")
    result = agent.action("每个歌手的专辑数量是多少？")
    print(result["answer"])
"""

import json
import os
import re
import sys
import traceback
from typing import Any, Dict, List, Tuple

# Windows 控制台默认 GBK 编码，可能打印不了部分字符导致崩溃，统一改为 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 数据库文件路径：基于本脚本所在目录解析，不受运行时工作目录影响
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chinook.db").replace("\\", "/")

import pandas as pd
import requests
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text


# ============================================================
# 1. 数据库解析 DBParser
# ============================================================
class DBParser:
    """解析数据库结构：表字段、外键关系、数据样例，并提供 SQL 校验与执行"""

    def __init__(self, db_url: str):
        self.engine = create_engine(db_url, echo=False)
        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()
        if not self.table_names:
            raise ValueError(f"未找到任何数据表，请检查数据库连接：{db_url}")

        # 收集所有外键关系
        self.foreign_keys = []
        for table_name in self.table_names:
            for fk in self.inspector.get_foreign_keys(table_name):
                self.foreign_keys.append({
                    "constrained_table": table_name,
                    "constrained_columns": fk["constrained_columns"],
                    "referred_table": fk["referred_table"],
                    "referred_columns": fk["referred_columns"],
                })

    def get_table_columns(self, table_name: str) -> List[str]:
        return [c["name"] for c in self.inspector.get_columns(table_name)]

    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        cols = self.inspector.get_columns(table_name)
        return pd.DataFrame([{"name": c["name"], "type": str(c["type"])} for c in cols])

    def get_data_relations(self) -> pd.DataFrame:
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str, n: int = 3) -> pd.DataFrame:
        t = Table(table_name, MetaData(), autoload_with=self.engine)
        with self.engine.connect() as conn:
            rows = conn.execute(select(t).limit(n)).all()
        cols = self.get_table_columns(table_name)
        return pd.DataFrame([tuple(r) for r in rows], columns=cols)

    def check_sql(self, sql: str) -> Tuple[bool, str]:
        """校验 SQL 是否可执行，返回 (是否成功, 错误信息)"""
        try:
            with self.engine.connect() as conn:
                conn.execute(text(sql))
            return True, "ok"
        except Exception:
            return False, traceback.format_exc()

    def execute_sql(self, sql: str) -> pd.DataFrame:
        """执行 SQL，返回带列名的 DataFrame"""
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = [tuple(r) for r in result]
        return pd.DataFrame(rows, columns=columns)


# ============================================================
# 2. 大模型客户端（DeepSeek，OpenAI 兼容接口）
# ============================================================
class LLMClient:
    def __init__(self, api_key: str, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com"):
        self.api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.1,
             max_retries: int = 3) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "model": self.model,
            "temperature": temperature,
            "messages": messages,
        }
        for attempt in range(max_retries):
            try:
                resp = requests.post(self.url, headers=headers, json=data, timeout=120)
                body = resp.json()
                if resp.status_code != 200 or "choices" not in body:
                    raise RuntimeError(f"HTTP {resp.status_code}: {body}")
                return body["choices"][0]["message"]["content"]
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                print(f"[LLM] 第 {attempt + 1} 次调用失败，重试: {type(e).__name__}: {e}")
        return ""


# ============================================================
# 3. SQL Agent
# ============================================================
class SQLAgent:
    def __init__(self, db_url: str, api_key: str, model: str = "deepseek-chat",
                 max_rewrite: int = 3, verbose: bool = True):
        self.parser = DBParser(db_url)
        self.llm = LLMClient(api_key, model=model)
        self.max_rewrite = max_rewrite
        self.verbose = verbose

        # 各步骤的提示词模板
        self._table_select_prompt = (
            "你是数据库专家。请根据用户问题，从下面的候选表中选择回答问题所需的表。\n\n"
            "候选表及其字段：\n{tables_desc}\n\n"
            "用户问题：{question}\n\n"
            "请只输出一个 JSON 数组，元素为需要的表名（必须从候选表名中选取），例如 [\"tracks\", \"albums\"]。"
        )
        self._sql_gen_prompt = (
            "你是数据库专家。请根据用户问题和相关表结构，生成一条 SQL 查询语句（SQLite 语法）。\n"
            "只输出 SQL 本身，不要用 markdown 代码块包裹，不要输出任何解释。\n\n"
            "用户问题：{question}\n\n"
            "相关表结构：\n{schema_context}\n\n"
            "表之间的外键关系：\n{relations}"
        )
        self._sql_rewrite_prompt = (
            "上面生成的 SQL 执行出错，请根据报错信息修正 SQL。\n"
            "只输出修正后的 SQL 本身，不要用 markdown 代码块包裹，不要输出任何解释。\n\n"
            "用户问题：{question}\n\n"
            "相关表结构：\n{schema_context}\n\n"
            "错误的 SQL：\n{sql}\n\n"
            "报错信息：\n{error}"
        )
        self._summary_prompt = (
            "你是答案汇总专家。请把用户的问题和 SQL 执行结果汇总成简洁的自然语言回答（用中文）。\n\n"
            "用户问题：{question}\n\n"
            "执行的 SQL：\n{sql}\n\n"
            "执行结果：\n{result}"
        )

    # ---------------- 辅助方法 ----------------
    def _build_tables_desc(self) -> str:
        lines = []
        for t in self.parser.table_names:
            cols = ", ".join(self.parser.get_table_columns(t))
            lines.append(f"- {t}: {cols}")
        return "\n".join(lines)

    def _build_schema_context(self, table_names: List[str]) -> Tuple[str, str]:
        """返回 (所选表的结构说明, 相关外键关系)"""
        parts = []
        for t in table_names:
            fields = self.parser.get_table_fields(t)
            field_str = ", ".join(f"{r['name']} ({r['type']})" for _, r in fields.iterrows())
            sample = self.parser.get_table_sample(t)
            parts.append(f"### 表 {t}\n字段: {field_str}\n样例:\n{sample.to_markdown()}")
        relations = self.parser.get_data_relations()
        if not relations.empty:
            relations = relations[relations["constrained_table"].isin(table_names)]
        relations_str = relations.to_markdown() if not relations.empty else "无"
        return "\n\n".join(parts), relations_str

    @staticmethod
    def _extract_sql(text: str) -> str:
        """从大模型输出中抽取干净的 SQL（去掉 markdown 代码块、分号等）"""
        text = (text or "").strip()
        m = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, re.S)
        if m:
            text = m.group(1)
        text = text.strip().strip("`").strip()
        return text.rstrip(";").strip()

    def _parse_tables(self, text: str) -> List[str]:
        """解析大模型返回的表名列表，并校验合法性"""
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
                valid = [t for t in arr if t in self.parser.table_names]
                if valid:
                    return valid
            except Exception:
                pass
        # 兜底：扫描文本中出现的表名
        valid = [t for t in self.parser.table_names if t in text]
        return valid or [self.parser.table_names[0]]

    # ---------------- 流程步骤 ----------------
    def select_tables(self, question: str) -> List[str]:
        """2. table 选择"""
        prompt = self._table_select_prompt.format(
            question=question,
            tables_desc=self._build_tables_desc(),
        )
        out = self.llm.chat([{"role": "user", "content": prompt}])
        tables = self._parse_tables(out)
        if self.verbose:
            print(f"[table 选择] -> {tables}")
        return tables

    def generate_sql(self, question: str, tables: List[str]) -> str:
        """3. SQL 生成"""
        schema, relations = self._build_schema_context(tables)
        prompt = self._sql_gen_prompt.format(
            question=question,
            schema_context=schema,
            relations=relations,
        )
        out = self.llm.chat([{"role": "user", "content": prompt}])
        sql = self._extract_sql(out)
        if self.verbose:
            print(f"[SQL 生成] -> {sql}")
        return sql

    def rewrite_sql(self, question: str, tables: List[str], sql: str, error: str) -> str:
        """5. SQL 改写"""
        schema, relations = self._build_schema_context(tables)
        prompt = self._sql_rewrite_prompt.format(
            question=question,
            schema_context=schema,
            sql=sql,
            error=error,
        )
        out = self.llm.chat([{"role": "user", "content": prompt}])
        new_sql = self._extract_sql(out)
        if self.verbose:
            print(f"[SQL 改写] -> {new_sql}")
        return new_sql

    def check_and_rewrite(self, question: str, tables: List[str], sql: str) -> str:
        """4. SQL 校验 + 5. SQL 改写（循环直到合法或达到最大次数）"""
        for _ in range(self.max_rewrite):
            ok, msg = self.parser.check_sql(sql)
            if ok:
                return sql
            if self.verbose:
                print(f"[SQL 校验] 失败，准备改写")
            sql = self.rewrite_sql(question, tables, sql, msg)
        # 最后一次校验
        ok, msg = self.parser.check_sql(sql)
        if not ok and self.verbose:
            print(f"[SQL 校验] 仍失败:\n{msg}")
        return sql

    def execute(self, sql: str) -> pd.DataFrame:
        """6. SQL 执行"""
        df = self.parser.execute_sql(sql)
        if self.verbose:
            print(f"[SQL 执行] 返回 {len(df)} 行")
        return df

    def summarize(self, question: str, sql: str, result: pd.DataFrame) -> str:
        """7. 结果汇总"""
        result_str = result.head(20).to_markdown()
        if len(result) > 20:
            result_str += f"\n...（共 {len(result)} 行）"
        prompt = self._summary_prompt.format(
            question=question, sql=sql, result=result_str,
        )
        out = self.llm.chat([{"role": "user", "content": prompt}], temperature=0.3)
        if self.verbose:
            print(f"[结果汇总] -> {out}")
        return out

    # ---------------- 智能体入口 ----------------
    def action(self, question: str) -> Dict[str, Any]:
        """完整流程入口：table 选择 -> SQL 生成 -> 校验/改写 -> 执行 -> 汇总"""
        tables = self.select_tables(question)          # 2. table 选择
        sql = self.generate_sql(question, tables)      # 3. SQL 生成
        sql = self.check_and_rewrite(question, tables, sql)  # 4+5. 校验 / 改写
        result = self.execute(sql)                     # 6. SQL 执行
        answer = self.summarize(question, sql, result) # 7. 结果汇总
        return {
            "question": question,
            "tables": tables,
            "sql": sql,
            "result": result,
            "answer": answer,
        }


# ============================================================
# 演示入口
# ============================================================
if __name__ == "__main__":
    agent = SQLAgent(
        db_url=f"sqlite:///{DB_PATH}",
        api_key="sk-5bcf61559aac41da944e1637fe6aa00d",
    )

    questions = [
        "统计每个歌手的专辑数量，按数量从高到低列出前 5 位歌手",
        "总销售额最高的客户是谁？",
    ]

    for q in questions:
        print("\n" + "=" * 70)
        print("问题：", q)
        result = agent.action(q)
        print("-" * 70)
        print("最终回答：", result["answer"])
