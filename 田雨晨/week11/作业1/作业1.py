from __future__ import annotations

import os
import re
import sys
import traceback
from typing import Callable

import pandas as pd
from sqlalchemy import (MetaData, Table, create_engine, func, inspect, select,
                        text)

DB_URL = "sqlite:///./chinook.db"


# ---------------------------------------------------------------- 环境修正
def _fix_no_proxy() -> None:
    # ponytail: 本机 NO_PROXY 含 "[::1]"，httpx 解析报 Invalid port，去掉方括号项即可。
    for key in ("no_proxy", "NO_PROXY"):
        if key in os.environ:
            items = [x.strip() for x in os.environ[key].split(",") if x.strip() and not x.startswith("[")]
            os.environ[key] = ",".join(items)


_fix_no_proxy()


# ---------------------------------------------------------------- 1. 数据库解析
class DBParser:
    """解析库结构，并负责 SQL 校验/执行（参考 sql-agent.ipynb 的 DBParser）。"""

    def __init__(self, db_url: str = DB_URL) -> None:
        self.db_url = db_url
        self.engine = create_engine(db_url, echo=False)
        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()
        self.foreign_keys: list[dict] = []
        self._fields: dict[str, dict] = {}
        self._sample: dict[str, pd.DataFrame] = {}

        with self.engine.connect() as conn:
            for name in self.table_names:
                print("[1] 解析表 ->", name)
                self.foreign_keys += [
                    {
                        "constrained_table": name,
                        "constrained_columns": fk["constrained_columns"],
                        "referred_table": fk["referred_table"],
                        "referred_columns": fk["referred_columns"],
                    }
                    for fk in self.inspector.get_foreign_keys(name)
                ]
                table = Table(name, MetaData(), autoload_with=self.engine)
                cols = self.inspector.get_columns(name)
                fields = {}
                for meta in cols:
                    col = getattr(table.columns, meta["name"])
                    info = dict(meta)
                    info["distinct"] = conn.execute(select(func.count(func.distinct(col)))).scalar()
                    info["nan_count"] = conn.execute(select(func.count()).where(col.is_(None))).scalar()
                    info["min"] = conn.execute(select(func.min(col))).scalar()
                    info["max"] = conn.execute(select(func.max(col))).scalar()
                    info["random"] = [
                        str(v) for v in conn.execute(select(col).limit(10)).scalars() if v is not None
                    ][:3]
                    if any(t in str(meta["type"]).lower() for t in ("text", "char")):
                        info["mode"] = conn.execute(
                            select(col).group_by(col).order_by(func.count().desc()).limit(1)
                        ).scalar()
                    fields[meta["name"]] = info
                self._fields[name] = fields
                row = conn.execute(select(table).limit(1)).fetchone()
                self._sample[name] = pd.DataFrame([row], columns=[c["name"] for c in cols])

    def get_table_fields(self, table_name: str) -> pd.DataFrame:
        return pd.DataFrame.from_dict(self._fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name: str) -> pd.DataFrame:
        return self._sample[table_name]

    def table_schema_text(self, table_name: str) -> str:
        """给 LLM 用的紧凑 schema（列名/类型/主键/取值特征）。"""
        lines = [f"表 {table_name}:"]
        for name, meta in self._fields[table_name].items():
            bits = [str(meta["type"])]
            if meta.get("primary_key"):
                bits.append("PK")
            bits.append(f"distinct={meta['distinct']}")
            if meta.get("nan_count"):
                bits.append(f"null={meta['nan_count']}")
            if meta.get("min") is not None or meta.get("max") is not None:
                bits.append(f"range=[{meta.get('min')}, {meta.get('max')}]")
            if meta.get("mode") is not None:
                bits.append(f"mode={meta['mode']!r}")
            if meta.get("random"):
                bits.append("e.g. " + ", ".join(repr(v) for v in meta["random"]))
            lines.append(f"  - {name} ({'; '.join(bits)})")
        return "\n".join(lines)

    def schema_text(self, table_names: list[str]) -> str:
        parts = [self.table_schema_text(t) for t in table_names]
        rels = [r for r in self.foreign_keys if r["constrained_table"] in table_names]
        if rels:
            parts.append(
                "外键:\n"
                + "\n".join(
                    f"  {r['constrained_table']}({', '.join(r['constrained_columns'])}) -> "
                    f"{r['referred_table']}({', '.join(r['referred_columns'])})"
                    for r in rels
                )
            )
        return "\n\n".join(parts)

    # ------------------------------------------------------------ 4. SQL 校验
    def check_sql(self, sql: str) -> tuple[bool, str]:
        """只校验不执行：EXPLAIN 能同时暴露语法错和未知表/列。"""
        statement = clean_sql(sql)
        if not statement:
            return False, "空 SQL"
        if not re.match(r"(?is)^(select|with)\b", statement):
            return False, "只允许 SELECT / WITH 查询"
        if ";" in statement:
            return False, "只允许单条语句"
        try:
            with self.engine.connect() as conn:
                conn.execute(text("EXPLAIN " + statement))
            return True, "ok"
        except Exception:
            return False, traceback.format_exc(limit=-1)

    # ------------------------------------------------------------ 6. SQL 执行
    def execute_sql(self, sql: str, max_rows: int = 50) -> list[tuple]:
        with self.engine.connect() as conn:
            result = conn.execute(text(clean_sql(sql)))
            return [tuple(r) for r in result.fetchmany(max_rows)]


def _first_error_line(message: str) -> str:
    """traceback 里挑出真正有用的那一行给人和 LLM 看。"""
    for line in message.strip().splitlines():
        if re.search(r"Error|error|no such|syntax", line):
            return line.strip()
    return message.strip().splitlines()[-1].strip() if message.strip() else "unknown error"


def clean_sql(raw: str) -> str:
    """去掉 markdown 围栏和多余说明，只留一条语句。"""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:sql)?\s*|\s*```$", "", s, flags=re.I).strip()
    match = re.search(r"(?is)\b(select|with)\b", s)
    if match:
        s = s[match.start():]
    return s.split(";")[0].strip()


# ---------------------------------------------------------------- LLM 层
SYSTEM_PROMPT = "你是一个严谨的 SQLite 数据分析专家，只输出要求的内容，不解释。"


def make_llm() -> Callable[[str], str]:
    """有 key 用真实模型，没有就走离线桩（保证 pipeline 可跑可测）。"""
    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[!] 未配置 DASHSCOPE_API_KEY / OPENAI_API_KEY，使用离线桩 LLM")
        return FakeLLM()

    import openai

    base_url = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    model = os.getenv("SQL_AGENT_MODEL", "qwen-plus")
    client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=60)

    def llm(prompt: str) -> str:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return completion.choices[0].message.content or ""

    return llm


class FakeLLM:
    """离线桩：按 prompt 里的任务标记返回固定答案，用于自检全链路。

    ponytail: 只覆盖 demo 问题；接真实模型后此类不参与运行。
    """

    def __call__(self, prompt: str) -> str:
        if "### 任务:选择表" in prompt:
            return '["artists", "albums"]'
        if "### 任务:生成SQL" in prompt:
            # 故意写错，用来验证 校验 -> 改写 分支
            return "SELECT ar.Name, COUNT(*) FROM albums GROUP BY ar.Name LIMIT 3"
        if "### 任务:改写SQL" in prompt:
            return (
                "SELECT ar.Name, COUNT(*) AS album_cnt FROM albums a "
                "JOIN artists ar ON a.ArtistId = ar.ArtistId "
                "GROUP BY ar.ArtistId ORDER BY album_cnt DESC LIMIT 3"
            )
        if "### 任务:汇总" in prompt:
            return "专辑数量最多的是 Iron Maiden（21 张），其后是 Led Zeppelin（14 张）和 Deep Purple（11 张）。"
        return ""


# ---------------------------------------------------------------- SQL Agent
class SQLAgentWorkflow:
    """table 选择 -> SQL 生成 -> 校验 -> 改写 -> 执行 -> 汇总，每步留痕。"""

    def __init__(self, parser: DBParser, llm: Callable[[str], str], max_retries: int = 3) -> None:
        self.parser = parser
        self.llm = llm
        self.max_retries = max_retries

    # 2. table 选择
    def select_tables(self, question: str) -> list[str]:
        prompt = (
            "### 任务:选择表\n"
            f"用户提问：{question}\n\n"
            f"可选表：{self.parser.table_names}\n\n"
            "请挑出回答该问题所需的表（含 JOIN 所需的中间表），"
            '只输出 JSON 数组，例如 ["albums", "artists"]。'
        )
        raw = self.llm(prompt)
        names = [n for n in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", raw) if n in self.parser.table_names]
        return list(dict.fromkeys(names))[:4] or self.parser.table_names[:1]

    # 3. SQL 生成
    def generate_sql(self, question: str, tables: list[str]) -> str:
        prompt = (
            "### 任务:生成SQL\n"
            f"用户提问：{question}\n\n"
            f"涉及表的结构：\n{self.parser.schema_text(tables)}\n\n"
            "请生成一条可直接执行的 SQLite 查询语句，只输出 SQL。"
        )
        return clean_sql(self.llm(prompt))

    # 5. SQL 改写
    def rewrite_sql(self, question: str, sql: str, error: str, tables: list[str]) -> str:
        prompt = (
            "### 任务:改写SQL\n"
            f"用户提问：{question}\n\n"
            f"有问题的 SQL：\n{sql}\n\n"
            f"校验/执行报错：\n{error[-1200:]}\n\n"
            f"涉及表的结构：\n{self.parser.schema_text(tables)}\n\n"
            "请依据报错修正 SQL，只输出修正后的完整 SQL。"
        )
        return clean_sql(self.llm(prompt))

    # 7. 结果汇总
    def summarize(self, question: str, sql: str, rows: list[tuple]) -> str:
        prompt = (
            "### 任务:汇总\n"
            f"用户提问：{question}\n\n"
            f"执行 SQL：{sql}\n\n"
            f"查询结果（前 {len(rows)} 行）：{rows[:20]}\n\n"
            "请用自然语言简洁回答该问题，数字与结果保持一致。"
        )
        return self.llm(prompt).strip()

    def run(self, question: str) -> dict:
        trace: dict = {"question": question, "attempts": []}
        print("[2] 选择表 ...")
        tables = self.select_tables(question)
        trace["tables"] = tables
        print("    ->", tables)

        print("[3] 生成 SQL ...")
        sql = self.generate_sql(question, tables)
        trace["sql"] = sql
        print("    ->", sql)

        rows: list[tuple] = []
        for attempt in range(1, self.max_retries + 1):
            print(f"[4] 校验 SQL（第 {attempt} 次）...")
            ok, message = self.parser.check_sql(sql)
            if ok:
                print("[6] 执行 SQL ...")
                try:
                    rows = self.parser.execute_sql(sql)
                    trace["attempts"].append({"attempt": attempt, "sql": sql, "error": None})
                    print(f"    -> {len(rows)} 行")
                    break
                except Exception:
                    message = traceback.format_exc(limit=-1)
            print("    !! 失败：", _first_error_line(message))
            trace["attempts"].append({"attempt": attempt, "sql": sql, "error": message})
            if attempt == self.max_retries:
                raise RuntimeError(f"SQL 在 {self.max_retries} 次重写后仍不可执行:\n{message}")
            print("[5] 改写 SQL ...")
            sql = self.rewrite_sql(question, sql, message, tables)
            print("    ->", sql)

        trace["sql_final"] = sql
        trace["rows"] = rows
        print("[7] 汇总结果 ...")
        trace["answer"] = self.summarize(question, sql, rows)
        print("    ->", trace["answer"])
        return trace


# ---------------------------------------------------------------- 入口
def build(db_url: str = DB_URL) -> SQLAgentWorkflow:
    return SQLAgentWorkflow(DBParser(db_url), make_llm())


def demo() -> None:
    trace = build().run("统计专辑数量最多的前 3 位艺术家")
    print("\n=== 最终 SQL ===\n" + trace["sql_final"])
    print("=== 结果 ===")
    for row in trace["rows"]:
        print(" ", row)
    print("=== 回答 ===\n" + trace["answer"])


def selftest() -> None:
    """离线自检：不依赖 API key，验证七步链路与改写重试。"""
    workflow = SQLAgentWorkflow(DBParser(DB_URL), FakeLLM())
    trace = workflow.run("统计专辑数量最多的前 3 位艺术家")

    assert trace["tables"] and set(trace["tables"]) <= set(workflow.parser.table_names)
    assert len(trace["attempts"]) >= 2, "应至少触发一次校验失败 + 改写"
    assert trace["attempts"][0]["error"], "首次生成应为坏 SQL"
    assert workflow.parser.check_sql(trace["sql_final"])[0]
    assert trace["rows"] and len(trace["rows"]) == 3
    assert trace["answer"]
    # 校验器自身：坏 SQL 必须被拒，好 SQL 必须放行
    assert not workflow.parser.check_sql("SELECT Name FROM album")[0]
    assert not workflow.parser.check_sql("DELETE FROM albums")[0]
    assert workflow.parser.check_sql("SELECT count(*) FROM albums;")[0]
    print("\n[ok] selftest passed")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg == "--selftest":
        selftest()
    elif arg in (None, "--demo"):
        demo()
    else:
        result = build().run(arg)
        print("\nSQL:", result["sql_final"])
        print("答案:", result["answer"])
