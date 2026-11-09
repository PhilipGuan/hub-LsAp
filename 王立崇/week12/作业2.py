# -*- coding: utf-8 -*-
"""
LangChain 复刻版 SQL Agent（ChatBI 数据智能问答）

对照 week11《作业1》里手写的 SQL Agent（7 步固定流水线），
改用 LangChain 的 create_agent + tool 重新实现一遍。

作业1 的流水线：
    table 选择 -> SQL 生成 -> SQL 校验 -> SQL 改写 -> SQL 执行 -> 结果汇总
这里把这套流程交给 LangChain 智能体自主完成，把数据库能力封装成三个工具：

    list_tables      -> 列出所有表          （对应作业1 的 DBParser.table_names）
    describe_tables  -> 查看表结构/外键/样例  （对应 get_table_fields / get_data_relations / get_table_sample）
    run_query        -> 执行 SQL，出错时返回报错 （对应 check_sql / execute_sql，改写循环由 agent 自动完成）

依赖：langchain / langchain-openai / sqlalchemy / pandas / tabulate（见 requirements.txt）

用法：
    python sql_agent_langchain.py
    或
    from sql_agent_langchain import create_sql_agent
    agent = create_sql_agent(model=..., db_url="sqlite:///./chinook.db")
    result = agent.invoke({"messages": [{"role": "user", "content": "每个歌手的专辑数量是多少？"}]})
"""

import os
import sys
from typing import List

# Windows 控制台默认 GBK 编码，可能打印不了部分字符导致崩溃，统一改为 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd
from sqlalchemy import create_engine, inspect, text

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI

# 数据库文件路径：基于本脚本所在目录解析，不受运行时工作目录影响（同作业1）
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chinook.db").replace("\\", "/")


# ============================================================
# 1. 数据库工具箱（对应作业1 的 DBParser）
# ============================================================
class SQLToolbox:
    """把作业1 里 DBParser 的能力（表结构 / 外键 / 样例 / SQL 执行）集中到这里。"""

    # 只读查询才放行，防止 agent 误执行增删改
    READ_ONLY_KEYWORDS = ("select", "with", "pragma", "explain")

    def __init__(self, db_url: str):
        self.engine = create_engine(db_url, echo=False)
        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()
        if not self.table_names:
            raise ValueError(f"未找到任何数据表，请检查数据库连接：{db_url}")

        # 收集所有外键关系（同作业1）
        self.foreign_keys = []
        for table_name in self.table_names:
            for fk in self.inspector.get_foreign_keys(table_name):
                self.foreign_keys.append({
                    "constrained_table": table_name,
                    "constrained_columns": fk["constrained_columns"],
                    "referred_table": fk["referred_table"],
                    "referred_columns": fk["referred_columns"],
                })

    # ---------------- 供工具调用 ----------------
    def list_tables(self) -> str:
        lines = [f"- {t}" for t in self.table_names]
        return f"数据库共有 {len(self.table_names)} 张表：\n" + "\n".join(lines)

    def describe_tables(self, table_names: List[str]) -> str:
        """返回所选表的结构说明 + 相关外键关系（同作业1 的 _build_schema_context）。"""
        valid = [t for t in table_names if t in self.table_names]
        invalid = [t for t in table_names if t not in self.table_names]
        if not valid:
            return f"表名无效：{invalid or table_names}。请先用 list_tables 确认存在的表名。"

        parts = []
        for t in valid:
            cols = self.inspector.get_columns(t)
            field_str = ", ".join(f"{c['name']} ({c['type']})" for c in cols)
            sample = self._sample(t)
            parts.append(f"### 表 {t}\n字段: {field_str}\n样例:\n{sample}")

        relations = self._relations_for(valid)
        relations_str = relations.to_markdown() if not relations.empty else "无"
        return "\n\n".join(parts) + f"\n\n相关外键关系:\n{relations_str}"

    def run_query(self, sql: str) -> str:
        """执行 SQL 并返回结果；出错时返回错误信息（供 agent 自行修正重试）。"""
        sql = (sql or "").strip().rstrip(";").strip()
        first_word = sql.split(None, 1)[0].lower() if sql else ""
        if first_word not in self.READ_ONLY_KEYWORDS:
            return (f"SQL 被拒绝：只允许只读查询（SELECT/WITH/PRAGMA/EXPLAIN），"
                    f"收到的是 '{first_word}'。请改为 SELECT 查询。")
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql))
                columns = list(result.keys())
                rows = [tuple(r) for r in result]
            df = pd.DataFrame(rows, columns=columns)
            if df.empty:
                return "查询成功，但返回 0 行。"
            limit = 50
            out = df.head(limit).to_markdown()
            if len(df) > limit:
                out += f"\n...（共 {len(df)} 行，仅显示前 {limit} 行）"
            return out
        except Exception as e:  # 把报错信息回传给 agent，让 agent 改写 SQL
            return f"SQL 执行出错：{type(e).__name__}: {e}"

    # ---------------- 内部辅助 ----------------
    def _sample(self, table_name: str, n: int = 3) -> str:
        cols = [c["name"] for c in self.inspector.get_columns(table_name)]
        with self.engine.connect() as conn:
            rows = conn.execute(text(f'SELECT * FROM "{table_name}" LIMIT {n}')).fetchall()
        df = pd.DataFrame([tuple(r) for r in rows], columns=cols)
        return df.to_markdown() if not df.empty else "（无数据）"

    def _relations_for(self, table_names: List[str]) -> pd.DataFrame:
        rels = pd.DataFrame(self.foreign_keys)
        if rels.empty:
            return rels
        return rels[rels["constrained_table"].isin(table_names)]


# ============================================================
# 2. 系统提示词（把作业1 各步骤的提示词合并成一份 agent 行为准则）
# ============================================================
SYSTEM_PROMPT = """你是一名专业的 ChatBI 数据分析助手，连接到一个 SQLite 数据库，用中文回答用户。

请严格按下面的流程工作：
1. 先用 list_tables 查看有哪些表。
2. 用 describe_tables 查看回答问题所需表的结构（字段名、类型、外键关系、样例数据）。
3. 根据问题写出 SQLite 方言的 SELECT 查询，用 run_query 执行。
4. 如果 run_query 返回了错误信息，仔细阅读错误、修正 SQL 后重试，直到成功或确认无法回答。
5. 拿到结果后，用简洁的中文把「问题 + 关键数据」组织成自然语言回答，直接给出结论和具体数字。

注意：
- 数据库方言是 SQLite：Top N 用 LIMIT、取前几行用 LIMIT，不要用 TOP；字符串用单引号。
- 表名/列名以 describe_tables 返回的为准，注意区分大小写。
- run_query 只允许只读查询（SELECT/WITH/PRAGMA/EXPLAIN），不要写增删改。
- 回答要落到具体数字和结论，不要只复述 SQL。"""


# ============================================================
# 3. 构建 SQL Agent：把数据库能力包装成 LangChain 工具
# ============================================================
def create_sql_agent(model: ChatOpenAI, db_url: str):
    """用 create_agent 构建 SQL Agent，工具闭包捕获数据库连接。"""
    toolbox = SQLToolbox(db_url)

    @tool
    def list_tables() -> str:
        """列出数据库中所有的数据表名称。写 SQL 之前先调用，以了解有哪些表可用。"""
        return toolbox.list_tables()

    @tool
    def describe_tables(table_names: List[str]) -> str:
        """查看指定数据表的结构：字段名、字段类型、外键关系，以及前 3 行样例数据。

        Args:
            table_names: 需要查看结构的表名列表，例如 ["albums", "artists"]。
        """
        return toolbox.describe_tables(table_names)

    @tool
    def run_query(sql: str) -> str:
        """在 SQLite 数据库上执行一条只读查询（SELECT/WITH/PRAGMA/EXPLAIN），返回结果表格。
        如果 SQL 有语法或逻辑错误，会返回错误信息，请根据错误信息修正 SQL 后重试。

        Args:
            sql: 要执行的 SQL 查询语句（SQLite 方言，只读）。
        """
        return toolbox.run_query(sql)

    return create_agent(
        model=model,
        tools=[list_tables, describe_tables, run_query],
        system_prompt=SYSTEM_PROMPT,
    )


# ============================================================
# 4. 演示数据库（作业1 依赖 chinook.db，这里内置一份迷你样例，缺失时自动生成）
# ============================================================
def create_demo_db(db_url: str) -> None:
    """生成一个迷你 Chinook 风格的演示库，足以回答作业1 的两个示例问题。"""
    engine = create_engine(db_url)
    # sqlite3 驱动一次只能执行一条语句，这里逐条建表
    ddl_statements = [
        "CREATE TABLE IF NOT EXISTS artists (ArtistId INTEGER PRIMARY KEY, Name TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS albums (AlbumId INTEGER PRIMARY KEY, Title TEXT NOT NULL, "
        "ArtistId INTEGER NOT NULL REFERENCES artists(ArtistId))",
        "CREATE TABLE IF NOT EXISTS tracks (TrackId INTEGER PRIMARY KEY, Name TEXT NOT NULL, "
        "AlbumId INTEGER NOT NULL REFERENCES albums(AlbumId), UnitPrice REAL)",
        "CREATE TABLE IF NOT EXISTS customers (CustomerId INTEGER PRIMARY KEY, FirstName TEXT, LastName TEXT)",
        "CREATE TABLE IF NOT EXISTS invoices (InvoiceId INTEGER PRIMARY KEY, Total REAL, "
        "CustomerId INTEGER NOT NULL REFERENCES customers(CustomerId))",
        "CREATE TABLE IF NOT EXISTS invoice_items (InvoiceLineId INTEGER PRIMARY KEY, UnitPrice REAL, Quantity INTEGER, "
        "InvoiceId INTEGER NOT NULL REFERENCES invoices(InvoiceId), "
        "TrackId INTEGER NOT NULL REFERENCES tracks(TrackId))",
    ]
    with engine.begin() as conn:
        for stmt in ddl_statements:
            conn.execute(text(stmt))
        if conn.execute(text("SELECT COUNT(*) FROM artists")).scalar() == 0:
            conn.execute(
                text("INSERT INTO artists (ArtistId, Name) VALUES (:ArtistId, :Name)"),
                [
                    {"ArtistId": 1, "Name": "Queen"},
                    {"ArtistId": 2, "Name": "The Beatles"},
                    {"ArtistId": 3, "Name": "Adele"},
                    {"ArtistId": 4, "Name": "Taylor Swift"},
                    {"ArtistId": 5, "Name": "Ed Sheeran"},
                    {"ArtistId": 6, "Name": "Coldplay"},
                    {"ArtistId": 7, "Name": "周杰伦"},
                    {"ArtistId": 8, "Name": "林俊杰"},
                ],
            )
            conn.execute(
                text("INSERT INTO albums (AlbumId, Title, ArtistId) VALUES (:AlbumId, :Title, :ArtistId)"),
                [
                    {"AlbumId": 1, "Title": "A Night at the Opera", "ArtistId": 1},
                    {"AlbumId": 2, "Title": "News of the World", "ArtistId": 1},
                    {"AlbumId": 3, "Title": "Abbey Road", "ArtistId": 2},
                    {"AlbumId": 4, "Title": "21", "ArtistId": 3},
                    {"AlbumId": 5, "Title": "25", "ArtistId": 3},
                    {"AlbumId": 6, "Title": "30", "ArtistId": 3},
                    {"AlbumId": 7, "Title": "1989", "ArtistId": 4},
                    {"AlbumId": 8, "Title": "folklore", "ArtistId": 4},
                    {"AlbumId": 9, "Title": "Divide", "ArtistId": 5},
                    {"AlbumId": 10, "Title": "Parachutes", "ArtistId": 6},
                    {"AlbumId": 11, "Title": "范特西", "ArtistId": 7},
                    {"AlbumId": 12, "Title": "叶惠美", "ArtistId": 7},
                    {"AlbumId": 13, "Title": "第二天堂", "ArtistId": 8},
                ],
            )
            conn.execute(
                text("INSERT INTO tracks (TrackId, Name, AlbumId, UnitPrice) VALUES (:TrackId, :Name, :AlbumId, :UnitPrice)"),
                [
                    {"TrackId": 1, "Name": "Bohemian Rhapsody", "AlbumId": 1, "UnitPrice": 0.99},
                    {"TrackId": 2, "Name": "We Are the Champions", "AlbumId": 2, "UnitPrice": 0.99},
                    {"TrackId": 3, "Name": "Come Together", "AlbumId": 3, "UnitPrice": 0.99},
                    {"TrackId": 4, "Name": "Rolling in the Deep", "AlbumId": 4, "UnitPrice": 1.29},
                    {"TrackId": 5, "Name": "Hello", "AlbumId": 5, "UnitPrice": 1.29},
                    {"TrackId": 6, "Name": "Shake It Off", "AlbumId": 7, "UnitPrice": 1.29},
                    {"TrackId": 7, "Name": "七里香", "AlbumId": 12, "UnitPrice": 0.99},
                    {"TrackId": 8, "Name": "江南", "AlbumId": 13, "UnitPrice": 0.99},
                ],
            )
            conn.execute(
                text("INSERT INTO customers (CustomerId, FirstName, LastName) VALUES (:CustomerId, :FirstName, :LastName)"),
                [
                    {"CustomerId": 1, "FirstName": "John", "LastName": "Smith"},
                    {"CustomerId": 2, "FirstName": "小明", "LastName": "王"},
                    {"CustomerId": 3, "FirstName": "华", "LastName": "李"},
                    {"CustomerId": 4, "FirstName": "Mary", "LastName": "Johnson"},
                ],
            )
            conn.execute(
                text("INSERT INTO invoices (InvoiceId, CustomerId, Total) VALUES (:InvoiceId, :CustomerId, :Total)"),
                [
                    {"InvoiceId": 1, "CustomerId": 1, "Total": 100.0},
                    {"InvoiceId": 2, "CustomerId": 1, "Total": 50.0},
                    {"InvoiceId": 3, "CustomerId": 2, "Total": 300.0},
                    {"InvoiceId": 4, "CustomerId": 3, "Total": 80.0},
                    {"InvoiceId": 5, "CustomerId": 4, "Total": 120.0},
                    {"InvoiceId": 6, "CustomerId": 4, "Total": 40.0},
                ],
            )
            conn.execute(
                text("INSERT INTO invoice_items (InvoiceLineId, InvoiceId, TrackId, UnitPrice, Quantity) "
                     "VALUES (:InvoiceLineId, :InvoiceId, :TrackId, :UnitPrice, :Quantity)"),
                [
                    {"InvoiceLineId": 1, "InvoiceId": 1, "TrackId": 1, "UnitPrice": 0.99, "Quantity": 1},
                    {"InvoiceLineId": 2, "InvoiceId": 3, "TrackId": 4, "UnitPrice": 1.29, "Quantity": 2},
                    {"InvoiceLineId": 3, "InvoiceId": 4, "TrackId": 6, "UnitPrice": 1.29, "Quantity": 1},
                    {"InvoiceLineId": 4, "InvoiceId": 5, "TrackId": 7, "UnitPrice": 0.99, "Quantity": 3},
                ],
            )
    print(f"[DB] 已生成演示数据库：{db_url}")


# ============================================================
# 5. 辅助函数
# ============================================================
def print_trace(result: dict) -> None:
    """打印 agent 的工具调用轨迹（对应作业1 的 verbose 输出）。"""
    for m in result.get("messages", []):
        mtype = getattr(m, "type", "")
        if mtype == "ai":
            for tc in getattr(m, "tool_calls", []) or []:
                print(f"  [tool 调用] {tc.get('name')} {tc.get('args')}")
        elif mtype == "tool":
            content = str(m.content)
            if len(content) > 300:
                content = content[:300] + " ..."
            print(f"  [tool 结果] {content}")


def get_final_answer(result: dict) -> str:
    """取出最后一条有文本内容的 AI 回复。"""
    for m in reversed(result.get("messages", [])):
        if getattr(m, "type", "") == "ai" and getattr(m, "content", ""):
            return m.content
    return "(无回答)"


# ============================================================
# 6. 演示入口
# ============================================================
if __name__ == "__main__":
    db_url = f"sqlite:///{DB_PATH}"
    if not os.path.exists(DB_PATH):
        create_demo_db(db_url)

    # 默认沿用作业1 的 DeepSeek（OpenAI 兼容接口）；推荐改成环境变量注入。
    # 也可换成教程里的通义千问：
    #   model="qwen-flash", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = ChatOpenAI(
        model=os.getenv("SQL_AGENT_MODEL", "deepseek-chat"),
        base_url=os.getenv("SQL_AGENT_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("DEEPSEEK_API_KEY", "sk-"),
        temperature=0.1,
    )

    agent = create_sql_agent(model, db_url)

    questions = [
        "统计每个歌手的专辑数量，按数量从高到低列出前 5 位歌手",
        "总销售额最高的客户是谁？",
    ]

    for q in questions:
        print("\n" + "=" * 70)
        print("问题：", q)
        result = agent.invoke({"messages": [{"role": "user", "content": q}]})
        print_trace(result)
        print("-" * 70)
        print("最终回答：", get_final_answer(result))
