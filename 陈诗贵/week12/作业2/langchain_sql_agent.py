# -*- coding: utf-8 -*-
"""
Week12 作业1 · LangChain 版 SQL Agent（基于 chinook.db）

用 LangChain 新版 API（create_agent + @tool）复刻七阶段 SQL Agent。
七个阶段 → 七个 tool，agent 自动编排调用链，失败时自动 rewrite 重试。

运行方式：
    python langchain_sql_agent.py -q "一共有多少首歌？"   单问题
    python langchain_sql_agent.py                       跑内置测试用例

依赖：
    pip install langchain langchain-openai sqlalchemy pandas
"""
import os
import re
import json
import traceback
from typing import List, Optional, Tuple

import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, inspect, MetaData, select, Table, text

# ======================================================================
# 配置区
# ======================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# chinook.db 查找顺序：week11/作业1 → 项目根目录 → 当前目录
_candidates = [
    os.path.normpath(os.path.join(BASE_DIR, "..", "..", "week11", "作业1", "chinook.db")),
    os.path.normpath(os.path.join(BASE_DIR, "..", "..", "..", "chinook.db")),
    os.path.join(BASE_DIR, "chinook.db"),
]
DB_PATH = next((p for p in _candidates if os.path.exists(p) and os.path.getsize(p) > 0), _candidates[0])
DB_URL = "sqlite:///" + DB_PATH.replace("\\", "/")

# ★★★ API Key ★★★
LLM_API_KEY = os.getenv("DS_API_KEY", os.getenv("LLM_API_KEY", ""))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-flash")

# 执行限制
MAX_TABLES_FOR_LLM = 3
MAX_RESULT_ROWS = 50
MAX_SUMMARY_ROWS = 20

# 安全黑名单（和原版 sql_agent_all.py 一致）
FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "replace", "attach", "detach", "pragma", "vacuum", "grant", "revoke",
    "begin", "commit", "rollback",
}


# ======================================================================
# 数据库解析器（复用原版 DBParser 逻辑）
# ======================================================================
class DBParser:
    def __init__(self, db_url: str) -> None:
        self.engine = create_engine(db_url, echo=False)
        self.conn = self.engine.connect()
        self.db_url = db_url
        self.inspector = inspect(self.engine)
        self.table_names: List[str] = self.inspector.get_table_names()
        self._table_fields = {}
        self.foreign_keys = []
        self._table_sample = {}
        self._scan()

    def _scan(self) -> None:
        for table_name in self.table_names:
            if table_name.startswith("sqlite_"):
                continue
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

    def _column_stats(self, column, column_meta) -> dict:
        stats = {}
        stats["distinct"] = self.conn.execute(
            select(func.count(func.distinct(column)))
        ).fetchone()[0]
        field_type = str(column_meta["type"]).lower()
        if "text" in field_type or "char" in field_type:
            top1 = self.conn.execute(
                select(column, func.count().label("count"))
                .group_by(column).order_by(func.count().desc()).limit(1)
            ).fetchone()
            stats["mode"] = top1[0] if top1 else None
        stats["nan_count"] = self.conn.execute(
            select(func.count()).where(column.is_(None))
        ).fetchone()[0]
        stats["max"] = self.conn.execute(select(func.max(column))).fetchone()[0]
        stats["min"] = self.conn.execute(select(func.min(column))).fetchone()[0]
        rows = self.conn.execute(select(column).limit(10)).fetchall()
        values = [str(r[0]) for r in rows if r[0] is not None]
        stats["random"] = list(dict.fromkeys(values))[:3]
        return stats

    def _read_sample(self, table_instance, table_columns) -> pd.DataFrame:
        rows = self.conn.execute(select(table_instance).limit(3)).fetchall()
        df = pd.DataFrame(rows)
        if df.empty:
            df = pd.DataFrame(columns=[c["name"] for c in table_columns])
        else:
            df.columns = [c["name"] for c in table_columns]
        return df

    def get_schema_text(self, table_name: str) -> str:
        fields = pd.DataFrame.from_dict(self._table_fields[table_name]).T
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
        df = self._table_sample[table_name]
        if df.empty:
            return f"表 {table_name} 为空表"
        return df.to_string(index=False)

    def get_all_table_summary(self) -> str:
        blocks = []
        for table_name in self.table_names:
            if table_name.startswith("sqlite_"):
                continue
            columns = list(self._table_fields[table_name].keys())
            blocks.append(f"表 {table_name}，字段：{', '.join(columns)}")
        return "\n".join(blocks)

    def get_relations_text(self, tables: List[str] = None) -> str:
        rows = self.foreign_keys
        if not rows:
            return ""
        if tables:
            rows = [r for r in rows
                    if r["constrained_table"] in tables and r["referred_table"] in tables]
        return "\n".join(
            f"{r['constrained_table']}.{r['constrained_columns']} -> "
            f"{r['referred_table']}.{r['referred_columns']}"
            for r in rows
        )

    def check_sql(self, sql: str) -> Tuple[bool, str]:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("EXPLAIN " + sql))
            return True, "ok"
        except Exception:
            return False, traceback.format_exc(limit=3)

    def execute_sql(self, sql: str) -> Tuple[pd.DataFrame, str]:
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql))
                rows = result.fetchall()
                columns = list(result.keys())
            return pd.DataFrame(rows, columns=columns), ""
        except Exception:
            return pd.DataFrame(), traceback.format_exc(limit=3)

    def close(self) -> None:
        self.conn.close()
        self.engine.dispose()


# ======================================================================
# 安全检查（和原版 sql_agent_all.py 一致）
# ======================================================================
def _security_check(sql: str) -> Tuple[bool, str]:
    if not sql.strip():
        return False, "SQL 为空"
    no_comment = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    no_comment = re.sub(r"--[^\n]*", " ", no_comment)
    no_string = re.sub(r"'([^']|'')*'", "''", no_comment)
    cleaned = no_string.strip().rstrip(";").strip()
    if ";" in cleaned:
        return False, "不允许一次执行多条 SQL"
    first_word = cleaned.split()[0].lower() if cleaned.split() else ""
    if first_word not in ("select", "with"):
        return False, f"只允许 SELECT 查询，当前语句以 {first_word} 开头"
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", cleaned.lower()):
            return False, f"SQL 中包含被禁用的关键字：{keyword}"
    return True, "ok"


def _norm_tables(raw, valid_names: List[str]) -> List[str]:
    """把 LLM 返回的表名列表规范化为纯字符串。
    LLM 有时会输出 [{"name": "invoices"}] 而不是 ["invoices"]，统一在这里兜住。"""
    out = []
    for item in raw or []:
        if isinstance(item, dict):
            item = item.get("name") or item.get("table") or item.get("table_name") or ""
        if isinstance(item, str):
            name = item.strip()
            if name and name in valid_names:
                out.append(name)
    return list(dict.fromkeys(out))[:MAX_TABLES_FOR_LLM]


def _clean_sql(sql: str) -> str:
    sql = sql.strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.I)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip().rstrip(";")


# ======================================================================
# LangChain 七个 Tool
# ======================================================================
# 全局解析器（scan_db 初始化一次后复用）
_parser: Optional[DBParser] = None


def _get_parser() -> DBParser:
    global _parser
    if _parser is None:
        _parser = DBParser(DB_URL)
    return _parser


# ---- Tool 1：数据库解析 ----
class ScanDbInput(BaseModel):
    pass


# 直接用 @tool，args_schema 不传也行，但传了更清晰
from langchain_core.tools import tool


@tool
def scan_db() -> str:
    """扫描数据库结构，返回所有表的 schema、外键关系和样例数据。
    必须最先调用一次，其他工具依赖本次扫描结果。"""
    parser = _get_parser()
    tables = [t for t in parser.table_names if not t.startswith("sqlite_")]

    # 构建紧凑的 JSON 结构，便于 agent 在对话历史里引用
    db_info = {
        "tables": [],
        "relations": parser.get_relations_text() or "无外键关系",
    }
    for t in tables:
        db_info["tables"].append({
            "name": t,
            "schema": parser.get_schema_text(t),
            "sample": parser.get_sample_text(t),
        })
    return json.dumps(db_info, ensure_ascii=False, indent=2)


# ---- Tool 2：选表 ----
class SelectTablesInput(BaseModel):
    question: str = Field(description="用户的自然语言问题")
    db_scan_json: str = Field(description="scan_db 返回的 JSON 字符串")


@tool
def select_tables(question: str, db_scan_json: str) -> str:
    """根据用户问题，从 scan_db 的结果中挑选相关表。
    最多选 3 张；如果问题与数据库无关，返回空列表。"""
    parser = _get_parser()
    try:
        info = json.loads(db_scan_json)
        all_tables_block = "\n".join(
            f"表 {t['name']}，字段：{', '.join(parser._table_fields[t['name']].keys())}"
            for t in info["tables"]
        )
    except Exception:
        all_tables_block = parser.get_all_table_summary()

    # 让 LLM 选表
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model=LLM_MODEL, base_url=LLM_BASE_URL, api_key=LLM_API_KEY, temperature=0)

    prompt = f"""你是数据库专家。根据用户问题，从下面列出的表中选最多 {MAX_TABLES_FOR_LLM} 张最相关的表。
    如果问题与数据库无关，返回空 JSON 数组。
    只输出 JSON 数组，不要解释。

    全部表及字段：
    {all_tables_block}

    外键关系：
    {parser.get_relations_text() or '无外键关系'}

    用户问题：{question}"""

    response = llm.invoke(prompt).content.strip()
    # 解析表名列表
    match = re.search(r"\[.*?\]", response, re.S)
    if match:
        try:
            tables = json.loads(match.group(0))
        except json.JSONDecodeError:
            tables = []
    else:
        tables = []
    # 过滤幻觉 + 规范化 dict/str 混合
    valid = _norm_tables(tables, parser.table_names)
    return json.dumps({"selected_tables": valid}, ensure_ascii=False)


# ---- Tool 3：SQL 生成 ----
class GenerateSqlInput(BaseModel):
    question: str = Field(description="用户的自然语言问题")
    selected_tables_json: str = Field(description="select_tables 返回的 JSON")


@tool
def generate_sql(question: str, selected_tables_json: str) -> str:
    """根据用户问题和选中表，生成一条 SELECT SQL。"""
    parser = _get_parser()
    try:
        info = json.loads(selected_tables_json)
        tables = _norm_tables(info.get("selected_tables"), parser.table_names)
    except Exception:
        return json.dumps({"sql": "", "error": "selected_tables_json 格式错误"})

    if not tables:
        return json.dumps({"sql": "", "error": "没有选中任何表，无法生成 SQL"})

    schema = "\n\n".join(parser.get_schema_text(t) for t in tables)
    samples = "\n\n".join(f"表 {t} 样例：\n{parser.get_sample_text(t)}" for t in tables)
    relations = parser.get_relations_text(tables) or "无外键关系"

    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model=LLM_MODEL, base_url=LLM_BASE_URL, api_key=LLM_API_KEY, temperature=0)

    prompt = f"""你是数据库专家，请根据用户问题生成 SQL 查询。
    只允许 SELECT 查询，禁止任何修改数据的语句。
    表名和字段名必须严格使用下面给出的真实名称。
    结果行数可能很多时，使用 ORDER BY 明确排序。
    只输出 SQL 语句，不要解释，不要加 ```sql 代码块。

    相关表结构：
    {schema}

    外键关系：
    {relations}

    数据样例：
    {samples}

    用户问题：{question}"""

    sql = _clean_sql(llm.invoke(prompt).content)
    return json.dumps({"sql": sql}, ensure_ascii=False)


# ---- Tool 4：SQL 校验 ----
class ValidateSqlInput(BaseModel):
    sql: str = Field(description="待校验的 SQL")


@tool
def validate_sql(sql: str) -> str:
    """校验 SQL：先做安全检查（只允许单条 SELECT），再用 EXPLAIN 做语法检查。
    返回 {"ok": true/false, "error": "..."}"""
    safe, reason = _security_check(sql)
    if not safe:
        return json.dumps({"ok": False, "error": f"安全检查未通过：{reason}"})

    parser = _get_parser()
    passed, error = parser.check_sql(sql)
    if not passed:
        return json.dumps({"ok": False, "error": f"语法检查未通过：{error}"})

    return json.dumps({"ok": True, "error": ""})


# ---- Tool 5：SQL 改写 ----
class RewriteSqlInput(BaseModel):
    question: str = Field(description="原始用户问题")
    sql: str = Field(description="有问题的 SQL")
    error: str = Field(description="validate_sql 返回的错误信息")


@tool
def rewrite_sql(question: str, sql: str, error: str) -> str:
    """根据 validate_sql 返回的错误信息，修正 SQL。"""
    parser = _get_parser()
    # 用哪些表？从 sql 里猜（FROM 后面的表名）
    match = re.search(r"\bfrom\s+([\w_,\s]+)", sql, re.I)
    if match:
        guessed_tables = [t.strip() for t in re.split(r"[,\s]+", match.group(1).strip()) if t.strip()]
    else:
        guessed_tables = []
    # 过滤有效表
    valid_tables = [t for t in guessed_tables if t in parser.table_names]
    schema = "\n\n".join(parser.get_schema_text(t) for t in valid_tables) if valid_tables else ""
    relations = parser.get_relations_text(valid_tables) or "无外键关系"

    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model=LLM_MODEL, base_url=LLM_BASE_URL, api_key=LLM_API_KEY, temperature=0)

    prompt = f"""你是数据库专家。下面这条 SQL 执行失败了，请修正它。
    只允许 SELECT 查询。只输出修正后的 SQL，不要解释，不要加代码块标记。

    相关表结构：
    {schema or '(未知，请参考错误信息自行推断)'}

    外键关系：
    {relations}

    原始问题：{question}

    有问题的 SQL：
    {sql}

    错误信息：
    {error}"""

    new_sql = _clean_sql(llm.invoke(prompt).content)
    return json.dumps({"sql": new_sql}, ensure_ascii=False)


# ---- Tool 6：SQL 执行 ----
class ExecuteSqlInput(BaseModel):
    sql: str = Field(description="要执行的 SQL（必须是 SELECT，且已通过 validate_sql）")


@tool
def execute_sql(sql: str) -> str:
    """执行 SQL，返回结果 JSON（包含 rows 和 cols）。
    自动追加 LIMIT 防止全表拉取。"""
    parser = _get_parser()

    # 自动补 LIMIT（已带 LIMIT/OFFSET 结尾就不追加，避免出现两个 LIMIT）
    truncated = False
    limited_sql = sql
    if not re.search(r"\blimit\s+\S+(\s+offset\s+\S+)?\s*$", sql.strip().lower()):
        limited_sql = sql.rstrip().rstrip(";") + f" LIMIT {MAX_RESULT_ROWS}"
        truncated = True

    df, error = parser.execute_sql(limited_sql)
    if error:
        return json.dumps({"ok": False, "error": error, "rows": [], "cols": []}, ensure_ascii=False)

    return json.dumps({
        "ok": True,
        "rows": df.to_dict(orient="records"),
        "cols": list(df.columns),
        "row_count": len(df),
        "truncated": truncated,
    }, ensure_ascii=False)


# ---- Tool 7：结果汇总 ----
class SummarizeInput(BaseModel):
    question: str = Field(description="用户的原始问题")
    sql: str = Field(description="实际执行的 SQL")
    result_json: str = Field(description="execute_sql 返回的 JSON 字符串")


@tool
def summarize_result(question: str, sql: str, result_json: str) -> str:
    """根据 SQL 执行结果，用自然语言回答用户问题。"""
    try:
        info = json.loads(result_json)
    except Exception:
        return "结果数据格式异常"

    if not info.get("ok"):
        return f"SQL 执行失败：{info.get('error', '未知错误')}"

    rows = info.get("rows", [])
    truncated = info.get("truncated", False)

    if not rows:
        result_text = "（查询结果为空，没有任何行）"
    else:
        # 转成 DataFrame 再 to_string
        df = pd.DataFrame(rows)
        result_text = df.head(MAX_SUMMARY_ROWS).to_string(index=False)
        if len(rows) > MAX_SUMMARY_ROWS:
            result_text += f"\n（共 {len(rows)} 行，仅展示前 {MAX_SUMMARY_ROWS} 行）"
        if truncated:
            result_text += f"\n（SQL 已自动追加 LIMIT {MAX_RESULT_ROWS}）"

    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model=LLM_MODEL, base_url=LLM_BASE_URL, api_key=LLM_API_KEY, temperature=0)

    prompt = f"""你是数据分析师。根据 SQL 查询结果，用自然语言回答用户问题。
    直接回答，语言简洁准确。结果为空就说明没查到，不要编造。不要输出 SQL 或分析过程。

    用户问题：{question}

    执行的 SQL：
    {sql}

    查询结果：
    {result_text}"""

    return llm.invoke(prompt).content.strip()


# ======================================================================
# Agent 组装
# ======================================================================
SYSTEM_PROMPT = """你是一个数据库问答 Agent，按以下七阶段流程回答用户的自然语言问题：

1. **scan_db** — 必须最先调用一次，扫描数据库结构，拿到所有表的 schema 和外键
2. **select_tables** — 根据用户问题，从 scan_db 结果里挑最相关的表，最多 3 张
3. **generate_sql** — 根据问题和选中表生成 SQL（只允许 SELECT）
4. **validate_sql** — 校验 SQL 的安全和语法
   - 如果 ok=false：记录错误 → 调 rewrite_sql 修正 → 再 validate_sql
   - 最多改写重试 3 次，还不通过就直接告诉用户 SQL 有问题
5. **execute_sql** — SQL 校验通过后执行
6. **summarize_result** — 根据执行结果用自然语言回答用户

**重要规则**：
- scan_db 只需调用一次，后续步骤的结果会留在对话历史里
- 所有工具返回的都是 JSON 字符串，需要解析后取字段
- 如果 select_tables 返回空表列表，说明问题和数据库无关，直接告知用户数据库里有什么类型的数据
- 安全优先：validate_sql 会拦截 INSERT/UPDATE/DELETE/DROP 等危险语句，不要尝试绕过
"""


def build_agent():
    """创建 LangChain agent。"""
    if not LLM_API_KEY:
        raise ValueError(
            "未配置 LLM API Key！请设置环境变量 DS_API_KEY 或 LLM_API_KEY，"
            "或在代码顶部填入 LLM_API_KEY。"
        )

    from langchain.agents import create_agent
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        temperature=0,
    )

    agent = create_agent(
        model=model,
        tools=[scan_db, select_tables, generate_sql, validate_sql, rewrite_sql, execute_sql, summarize_result],
        system_prompt=SYSTEM_PROMPT,
    )
    return agent


# ======================================================================
# 入口
# ======================================================================
TEST_CASES = [
    ("T1", "一共有多少首歌？"),
    ("T2", "销售额最高的前 5 个国家是哪些？"),
    ("T3", "每个流派的歌曲数量分别是多少？"),
    ("T4", "哪位员工的工龄最长？"),
    ("B1", "今天天气怎么样？"),
]


def run_question(agent, question: str) -> None:
    print(f"\n{'='*70}")
    print(f"问题：{question}")
    print(f"{'='*70}")

    result = agent.invoke({"messages": [{"role": "user", "content": question}]})

    # 从 agent 输出里提取最终回答
    final_answer = ""
    # agent 输出结构：{"messages": [...]}，最后一条是 AIMessage
    msgs = result.get("messages", [])
    if msgs:
        last = msgs[-1]
        final_answer = getattr(last, "content", str(last))

    print(f"\n回答：{final_answer}")
    print(f"\n完整消息链（{len(msgs)} 条）：")
    for i, m in enumerate(msgs):
        role = getattr(m, "type", "unknown")
        content = getattr(m, "content", str(m))
        # 截断长内容
        if isinstance(content, str) and len(content) > 200:
            content = content[:200] + "..."
        print(f"  [{i}] {role}: {content}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="LangChain 版 SQL Agent")
    parser.add_argument("-q", "--question", type=str, help="单个问题")
    parser.add_argument("--db", action="store_true", help="只跑数据库解析（不依赖 LLM）")
    args = parser.parse_args()

    # 模式一：只跑数据库解析
    if args.db:
        print("数据库：", DB_URL)
        db = DBParser(DB_URL)
        tables = [t for t in db.table_names if not t.startswith("sqlite_")]
        print(f"\n共解析到 {len(tables)} 张表：")
        for t in tables:
            print(f"\n--- {t} ---")
            print(db.get_schema_text(t))
        print("\n外键关系：")
        print(db.get_relations_text() or "无")
        db.close()
        return

    agent = build_agent()

    # 模式二：单问题
    if args.question:
        run_question(agent, args.question)
        return

    # 模式三：跑内置测试用例
    for case_id, question in TEST_CASES:
        print(f"\n\n########## 测试用例 {case_id} ##########")
        try:
            run_question(agent, question)
        except Exception as e:
            print(f"用例 {case_id} 执行异常：{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
