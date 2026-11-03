# -*- coding: utf-8 -*-
"""
Week13 作业1 · LangGraph 版 SQL Agent（chinook.db）

用 LangGraph 的 StateGraph 复现 Week12 的七阶段 SQL Agent。
每个阶段 = 一个节点（node），节点之间用**串行边**连接：

    START → scan_db → select_tables → generate_sql → validate_sql
                                                        │
                              ┌─────────────────────────┴─────────────┐
                              │ ok                                    │ 不通过（且未超重试上限）
                              ▼                                       ▼
                          execute_sql                            rewrite_sql ──┐
                              │                                       ▲         │
                              │                                       └─────────┘
                              ▼                                   （回到 validate_sql 重新校验）
                          summarize → END

只有 validate → rewrite 这一条是条件边（业务必需：SQL 不合法才需要改写），
其余全部是串行边。

运行：
    python langgraph_sql_agent.py -q "一共有多少首歌？"   # 单个问题
    python langgraph_sql_agent.py                        # 跑内置测试用例
    python langgraph_sql_agent.py --db                   # 只看数据库解析（不调 LLM）

依赖：
    pip install langgraph langchain-openai sqlalchemy pandas
"""
import os
import re
import json
import operator
import traceback
from typing import Annotated, List, Optional, Tuple, TypedDict

import pandas as pd
from sqlalchemy import create_engine, func, inspect, MetaData, select, Table, text

# ======================================================================
# 配置
# ======================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# chinook.db 查找顺序：作业/week11/作业1 → 项目根目录 → 当前目录
_candidates = [
    os.path.normpath(os.path.join(BASE_DIR, "..", "..", "week11", "作业1", "chinook.db")),
    os.path.normpath(os.path.join(BASE_DIR, "..", "..", "..", "chinook.db")),
    os.path.join(BASE_DIR, "chinook.db"),
]
DB_PATH = next((p for p in _candidates if os.path.exists(p) and os.path.getsize(p) > 0), _candidates[0])
DB_URL = "sqlite:///" + DB_PATH.replace("\\", "/")

# 从项目根目录的 .env 加载配置（root/.env，向上三级）
from dotenv import load_dotenv

_ENV_PATH = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "..", ".env"))
if os.path.exists(_ENV_PATH):
    load_dotenv(_ENV_PATH, override=False)

# API 配置（只从环境变量 / .env 读，不硬编码密钥）
LLM_API_KEY = os.getenv("DS_API_KEY", os.getenv("LLM_API_KEY", ""))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-flash")

MAX_TABLES_FOR_LLM = 3      # 选表上限
MAX_RESULT_ROWS = 50        # 执行和汇总共用的行数上限（两处必须一致，否则结果会被二次截断）
MAX_REWRITE = 3             # SQL 改写重试上限

FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "replace", "attach", "detach", "pragma", "vacuum", "grant", "revoke",
    "begin", "commit", "rollback",
}


# ======================================================================
# 数据库解析器
# ======================================================================
class DBParser:
    """扫描数据库结构：表、字段、类型、统计值、外键、样例行。"""

    def __init__(self, db_url: str) -> None:
        self.engine = create_engine(db_url, echo=False)
        self.conn = self.engine.connect()
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
                self._table_fields[table_name][column_meta["name"]].update(
                    self._column_stats(column, column_meta)
                )
            self._table_sample[table_name] = self._read_sample(table_instance, table_columns)

    def _column_stats(self, column, column_meta) -> dict:
        stats = {
            "distinct": self.conn.execute(
                select(func.count(func.distinct(column)))
            ).fetchone()[0],
            "nan_count": self.conn.execute(
                select(func.count()).where(column.is_(None))
            ).fetchone()[0],
            "max": self.conn.execute(select(func.max(column))).fetchone()[0],
            "min": self.conn.execute(select(func.min(column))).fetchone()[0],
        }
        field_type = str(column_meta["type"]).lower()
        if "text" in field_type or "char" in field_type:
            top1 = self.conn.execute(
                select(column, func.count().label("count"))
                .group_by(column).order_by(func.count().desc()).limit(1)
            ).fetchone()
            stats["mode"] = top1[0] if top1 else None
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

    def get_relations_text(self, tables: Optional[List[str]] = None) -> str:
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
        """用 EXPLAIN 做语法检查（不实际执行）。"""
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
# 工具函数
# ======================================================================
_parser: Optional[DBParser] = None
_llm = None


def get_parser() -> DBParser:
    """全局复用同一个解析器，避免每次节点都重扫数据库。"""
    global _parser
    if _parser is None:
        _parser = DBParser(DB_URL)
    return _parser


def get_llm():
    """LLM 单例。trust_env=False 绕过系统代理，避免 Clash 隧道 TLS 握手失败。"""
    global _llm
    if _llm is None:
        import httpx
        from langchain_openai import ChatOpenAI
        _llm = ChatOpenAI(
            model=LLM_MODEL,
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            temperature=0,
            http_client=httpx.Client(trust_env=False, timeout=60),
        )
    return _llm


def security_check(sql: str) -> Tuple[bool, str]:
    """只允许单条 SELECT / WITH 查询。"""
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


def norm_tables(raw, valid_names: List[str]) -> List[str]:
    """把 LLM 返回的 [[{"name":"x"}]] / ["x"] 统一成字符串列表并过滤幻觉。"""
    out = []
    for item in raw or []:
        if isinstance(item, dict):
            item = item.get("name") or item.get("table") or item.get("table_name") or ""
        if isinstance(item, str):
            name = item.strip()
            if name and name in valid_names:
                out.append(name)
    return list(dict.fromkeys(out))[:MAX_TABLES_FOR_LLM]


def clean_sql(sql: str) -> str:
    """剥掉 LLM 可能加的 ```sql 代码块标记和结尾分号。"""
    sql = sql.strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.I)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip().rstrip(";")


# ======================================================================
# 状态定义
# ======================================================================
class AgentState(TypedDict, total=False):
    """在节点之间流转的状态。total=False 表示所有字段都是可选的。"""
    question: str                  # 用户原始问题
    db_info: str                   # 阶段1 产出：数据库结构 JSON
    selected_tables: List[str]     # 阶段2 产出：选中的表
    sql: str                       # 阶段3/5 产出：SQL
    validate_ok: bool              # 阶段4 产出：是否校验通过
    error: str                     # 阶段4/5：错误信息
    result_json: str               # 阶段6 产出：查询结果 JSON
    answer: str                    # 阶段7 产出：最终自然语言答案
    attempts: int                  # 改写次数计数
    trace: Annotated[List[str], operator.add]  # 节点执行轨迹（累加）


# ======================================================================
# 七个节点（每个节点 = 一个阶段）
# ======================================================================
def node_scan_db(state: AgentState) -> dict:
    """阶段1 · 数据库解析：扫描所有表的 schema、外键、样例。"""
    parser = get_parser()
    tables = [t for t in parser.table_names if not t.startswith("sqlite_")]
    db_info = {
        "tables": [
            {
                "name": t,
                "schema": parser.get_schema_text(t),
                "sample": parser.get_sample_text(t),
            }
            for t in tables
        ],
        "relations": parser.get_relations_text() or "无外键关系",
    }
    return {
        "db_info": json.dumps(db_info, ensure_ascii=False),
        "trace": ["scan_db 完成：解析到 %d 张表" % len(tables)],
    }


def node_select_tables(state: AgentState) -> dict:
    """阶段2 · table 选择：根据问题挑最多 3 张相关表。"""
    parser = get_parser()
    try:
        info = json.loads(state.get("db_info", "{}"))
        all_tables_block = "\n".join(
            f"表 {t['name']}，字段：{', '.join(parser._table_fields[t['name']].keys())}"
            for t in info["tables"]
        )
    except Exception:
        all_tables_block = parser.get_all_table_summary()

    prompt = f"""你是数据库专家。根据用户问题，从下面列出的表中选最多 {MAX_TABLES_FOR_LLM} 张最相关的表。
如果问题与数据库无关，返回空 JSON 数组。
只输出 JSON 数组，不要解释。

全部表及字段：
{all_tables_block}

外键关系：
{parser.get_relations_text() or '无外键关系'}

用户问题：{state['question']}"""

    response = get_llm().invoke(prompt).content.strip()
    match = re.search(r"\[.*?\]", response, re.S)
    tables = []
    if match:
        try:
            tables = json.loads(match.group(0))
        except json.JSONDecodeError:
            tables = []
    valid = norm_tables(tables, parser.table_names)
    return {
        "selected_tables": valid,
        "trace": ["select_tables 完成：选中 %s" % (valid or "无")],
    }


def node_generate_sql(state: AgentState) -> dict:
    """阶段3 · SQL 生成：自然语言 → SQL。"""
    parser = get_parser()
    tables = state.get("selected_tables") or []
    if not tables:
        return {
            "sql": "",
            "validate_ok": False,
            "error": "没有选中任何表：该问题与数据库内容无关",
            "trace": ["generate_sql 跳过：无选中表"],
        }

    schema = "\n\n".join(parser.get_schema_text(t) for t in tables)
    samples = "\n\n".join(f"表 {t} 样例：\n{parser.get_sample_text(t)}" for t in tables)
    relations = parser.get_relations_text(tables) or "无外键关系"

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

用户问题：{state['question']}"""

    sql = clean_sql(get_llm().invoke(prompt).content)
    return {
        "sql": sql,
        "trace": ["generate_sql 完成：%s" % sql[:120]],
    }


def node_validate_sql(state: AgentState) -> dict:
    """阶段4 · SQL 校验：先安全检查，再 EXPLAIN 语法检查。"""
    sql = state.get("sql", "")
    if not sql:
        return {
            "validate_ok": False,
            "error": state.get("error", "SQL 为空"),
            "trace": ["validate_sql 跳过：SQL 为空"],
        }

    safe, reason = security_check(sql)
    if not safe:
        return {
            "validate_ok": False,
            "error": f"安全检查未通过：{reason}",
            "trace": ["validate_sql 未通过（安全）：%s" % reason],
        }

    passed, err = get_parser().check_sql(sql)
    if not passed:
        return {
            "validate_ok": False,
            "error": f"语法检查未通过：{err}",
            "trace": ["validate_sql 未通过（语法）"],
        }

    return {
        "validate_ok": True,
        "error": "",
        "trace": ["validate_sql 通过"],
    }


def node_rewrite_sql(state: AgentState) -> dict:
    """阶段5 · SQL 改写：根据校验错误修正 SQL。"""
    parser = get_parser()
    sql = state.get("sql", "")
    match = re.search(r"\bfrom\s+([\w_,\s]+)", sql, re.I)
    guessed = [t.strip() for t in re.split(r"[,\s]+", match.group(1).strip()) if t.strip()] if match else []
    valid_tables = [t for t in guessed if t in parser.table_names]
    schema = "\n\n".join(parser.get_schema_text(t) for t in valid_tables) if valid_tables else ""
    relations = parser.get_relations_text(valid_tables) or "无外键关系"

    prompt = f"""你是数据库专家。下面这条 SQL 执行失败了，请修正它。
只允许 SELECT 查询。只输出修正后的 SQL，不要解释，不要加代码块标记。

相关表结构：
{schema or '(未知，请参考错误信息自行推断)'}

外键关系：
{relations}

原始问题：{state['question']}

有问题的 SQL：
{sql}

错误信息：
{state.get('error', '')}"""

    new_sql = clean_sql(get_llm().invoke(prompt).content)
    return {
        "sql": new_sql,
        "attempts": state.get("attempts", 0) + 1,
        "trace": ["rewrite_sql 第 %d 次：%s" % (state.get("attempts", 0) + 1, new_sql[:120])],
    }


def node_execute_sql(state: AgentState) -> dict:
    """阶段6 · SQL 执行：返回结果（自动补 LIMIT 防止全表拉取）。"""
    sql = state.get("sql", "")
    if not sql:
        return {
            "result_json": json.dumps({"ok": False, "error": "SQL 为空", "rows": [], "cols": []}, ensure_ascii=False),
            "trace": ["execute_sql 跳过：SQL 为空"],
        }

    limited_sql = sql
    truncated = False
    if not re.search(r"\blimit\s+\S+(\s+offset\s+\S+)?\s*$", sql.strip().lower()):
        limited_sql = sql.rstrip().rstrip(";") + f" LIMIT {MAX_RESULT_ROWS}"
        truncated = True

    df, error = get_parser().execute_sql(limited_sql)
    if error:
        return {
            "result_json": json.dumps({"ok": False, "error": error, "rows": [], "cols": []}, ensure_ascii=False),
            "trace": ["execute_sql 失败"],
        }

    return {
        "result_json": json.dumps({
            "ok": True,
            "rows": df.to_dict(orient="records"),
            "cols": list(df.columns),
            "row_count": len(df),
            "truncated": truncated,
        }, ensure_ascii=False),
        "trace": ["execute_sql 完成：返回 %d 行" % len(df)],
    }


def node_summarize(state: AgentState) -> dict:
    """阶段7 · 结果汇总：把查询结果转成自然语言回答。"""
    # 上游失败（无表 / 校验多次未过 / 执行出错）→ 直接报告，不编造
    if not state.get("sql"):
        return {"answer": f"无法回答该问题：{state.get('error', '未生成 SQL')}", "trace": ["summarize 报告失败"]}

    try:
        info = json.loads(state.get("result_json", "{}"))
    except Exception:
        return {"answer": "结果数据格式异常", "trace": ["summarize 报告失败"]}

    if not info.get("ok"):
        return {
            "answer": f"SQL 执行失败：{info.get('error', '未知错误')}",
            "trace": ["summarize 报告失败"],
        }

    rows = info.get("rows", [])
    if not rows:
        result_text = "（查询结果为空，没有任何行）"
    else:
        df = pd.DataFrame(rows)
        result_text = df.head(MAX_RESULT_ROWS).to_string(index=False)
        if len(rows) > MAX_RESULT_ROWS:
            result_text += f"\n（共 {len(rows)} 行，仅展示前 {MAX_RESULT_ROWS} 行）"
        if info.get("truncated"):
            result_text += f"\n（SQL 已自动追加 LIMIT {MAX_RESULT_ROWS}）"

    prompt = f"""你是数据分析师。根据 SQL 查询结果，用自然语言回答用户问题。
直接回答，语言简洁准确。结果为空就说明没查到，不要编造。不要输出 SQL 或分析过程。

用户问题：{state['question']}

执行的 SQL：
{state.get('sql', '')}

查询结果：
{result_text}"""

    answer = get_llm().invoke(prompt).content.strip()
    return {"answer": answer, "trace": ["summarize 完成"]}


# ======================================================================
# 条件边：校验通过 → 执行；不通过 → 改写（超上限则直接汇总报错）
# ======================================================================
def route_after_validate(state: AgentState) -> str:
    # 没有 SQL（如问题与数据库无关）→ 直接汇总报告，不要进 rewrite 编造语句
    if not state.get("sql"):
        return "summarize"
    if state.get("validate_ok"):
        return "execute_sql"
    if state.get("attempts", 0) >= MAX_REWRITE:
        return "summarize"
    return "rewrite_sql"


# ======================================================================
# 构建图
# ======================================================================
def build_graph():
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(AgentState)

    # 注册七个节点
    builder.add_node("scan_db", node_scan_db)
    builder.add_node("select_tables", node_select_tables)
    builder.add_node("generate_sql", node_generate_sql)
    builder.add_node("validate_sql", node_validate_sql)
    builder.add_node("rewrite_sql", node_rewrite_sql)
    builder.add_node("execute_sql", node_execute_sql)
    builder.add_node("summarize", node_summarize)

    # 串行边
    builder.add_edge(START, "scan_db")
    builder.add_edge("scan_db", "select_tables")
    builder.add_edge("select_tables", "generate_sql")
    builder.add_edge("generate_sql", "validate_sql")

    # 唯一的分支边：校验结果决定走执行还是改写
    builder.add_conditional_edges(
        "validate_sql",
        route_after_validate,
        {"execute_sql": "execute_sql", "rewrite_sql": "rewrite_sql", "summarize": "summarize"},
    )

    builder.add_edge("rewrite_sql", "validate_sql")   # 改完再校验
    builder.add_edge("execute_sql", "summarize")
    builder.add_edge("summarize", END)

    return builder.compile()


# ======================================================================
# 入口
# ======================================================================
TEST_CASES = [
    ("T1", "一共有多少首歌？", "3503"),
    ("T2", "销售额最高的前 5 个国家是哪些？", "USA 最高"),
    ("T3", "每个流派的歌曲数量分别是多少？", "25 个流派"),
    ("T4", "哪位员工的工龄最长？", "Jane Peacock"),
    ("B1", "今天天气怎么样？", "应拒绝"),
    ("B2", "帮我删除所有订单记录", "应被安全拦截"),
]


def run_one(graph, question: str) -> AgentState:
    print(f"\n{'=' * 72}")
    print(f"问题：{question}")
    print("=" * 72)

    state = graph.invoke({"question": question, "attempts": 0, "trace": []})

    print("\n节点轨迹：")
    for i, line in enumerate(state.get("trace", []), 1):
        print(f"  {i}. {line}")
    print(f"\n最终 SQL：\n  {state.get('sql') or '(无)'}")
    print(f"\n回答：\n  {state.get('answer') or '(无)'}")
    return state


def main():
    import argparse
    ap = argparse.ArgumentParser(description="LangGraph 版 SQL Agent")
    ap.add_argument("-q", "--question", type=str, help="单个问题")
    ap.add_argument("--db", action="store_true", help="只跑数据库解析（不调 LLM）")
    args = ap.parse_args()

    if args.db:
        print("数据库：", DB_URL)
        db = DBParser(DB_URL)
        tables = [t for t in db.table_names if not t.startswith("sqlite_")]
        print(f"共解析到 {len(tables)} 张表：{', '.join(tables)}")
        print("\n外键关系：")
        print(db.get_relations_text() or "无")
        db.close()
        return

    if not LLM_API_KEY:
        raise SystemExit(
            "未配置 API Key。请先设置环境变量：\n"
            "  $env:DS_API_KEY=\"你的key\"\n"
            "  $env:NO_PROXY=\"*\""
        )

    graph = build_graph()

    if args.question:
        run_one(graph, args.question)
        return

    for case_id, question, expect in TEST_CASES:
        print(f"\n\n########## {case_id}（期望：{expect}）##########")
        try:
            run_one(graph, question)
        except Exception as e:
            print(f"用例 {case_id} 异常：{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
