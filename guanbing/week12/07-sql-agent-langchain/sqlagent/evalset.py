"""冻结验证集 + 问题模拟生成器。

三部分：

1. **`GOLDEN_SET`（12 题）**：一次编好、只读。`ref_rows` 由手写 `ref_sql` 经
   **双引擎（SQLAlchemy + 原生 sqlite3）交叉验证**后固化（`freeze_golden()`）。
   评测时只读、绝不重算 —— 既保证可复现，也让「答案」独立于「答题模型」，避免自证偏差。
   9 题 in（count / join / topk 三类）+ 2 题 out + 1 题 ambiguous。

2. **`STRUCTURE_PROBES`（5 题）**：**结构探针**，专门覆盖 GOLDEN_SET 没覆盖的 5 种 SQL
   结构（HAVING / 标量子查询 / 自连接 / 日期分桶 / CTE+窗口函数），用来照亮 L2 在复杂结构上
   的盲区。用法与 GOLDEN_SET 不同：**是探针，不是硬门槛**（见下方常量处的说明）。

3. **`QuestionSimulator`**：用 LLM 对着表结构造题（可指定题型与是否「用户口吻」），
   以及造**越界负样本**。原 notebook 的「模拟生成问题」就是这个生成器的雏形，
   原先的 `build_eval_set()` 是它的特化版（只造单值统计题）。

> 造题与判分用同类模型，存在自证偏差；它只用于构造测试 / 演示数据，不代表真实用户分布。
"""

from __future__ import annotations

import json
import re

from . import llm, prompts
from .config import find_up, get_db_url
from .db import DBParser
from .guard import is_readonly

# --------------------------------------------------------------------------- #
# 冻结验证集
# --------------------------------------------------------------------------- #

GOLDEN_SET = [
    {"id": "G1", "question": "数据库里一共有多少首歌曲？", "scope": "in",
     "tables": ["tracks"],
     "ref_sql": "SELECT COUNT(*) AS track_count FROM tracks;",
     "ref_rows": [(3503,)],
     "ref_answer_keypoints": ["3503"], "tags": ["count"]},

    {"id": "G2", "question": "数据库里一共有多少张专辑？", "scope": "in",
     "tables": ["albums"],
     "ref_sql": "SELECT COUNT(*) AS album_count FROM albums;",
     "ref_rows": [(347,)],
     "ref_answer_keypoints": ["347"], "tags": ["count"]},

    {"id": "G3", "question": "数据库里一共有多少位客户？", "scope": "in",
     "tables": ["customers"],
     "ref_sql": "SELECT COUNT(*) AS customer_count FROM customers;",
     "ref_rows": [(59,)],
     "ref_answer_keypoints": ["59"], "tags": ["count"]},

    {"id": "G4", "question": "数据库里一共有多少名员工？", "scope": "in",
     "tables": ["employees"],
     "ref_sql": "SELECT COUNT(*) AS employee_count FROM employees;",
     "ref_rows": [(8,)],
     "ref_answer_keypoints": ["8"], "tags": ["count"]},

    {"id": "G5", "question": "账单国家是德国的发票有多少张？", "scope": "in",
     "tables": ["invoices"],
     "ref_sql": "SELECT COUNT(*) AS invoice_count FROM invoices WHERE BillingCountry = 'Germany';",
     "ref_rows": [(28,)],
     "ref_answer_keypoints": ["28"], "tags": ["count", "filter"]},

    {"id": "G6", "question": "销量最高的 5 个流派分别是什么，各卖了多少？", "scope": "in",
     "tables": ["genres", "tracks", "invoice_items"],
     "ref_sql": ("SELECT g.Name AS genre, SUM(ii.Quantity) AS sold "
                 "FROM genres g JOIN tracks t ON t.GenreId = g.GenreId "
                 "JOIN invoice_items ii ON ii.TrackId = t.TrackId "
                 "GROUP BY g.GenreId, g.Name ORDER BY sold DESC LIMIT 5;"),
     "ref_rows": [("Rock", 835), ("Latin", 386), ("Metal", 264),
                  ("Alternative & Punk", 244), ("Jazz", 80)],
     "ref_answer_keypoints": ["Rock", "835", "Latin", "386"],
     "tags": ["join", "aggregate", "topk"]},

    {"id": "G7", "question": "每位客服各自负责多少位客户？", "scope": "in",
     "tables": ["employees", "customers"],
     "ref_sql": ("SELECT e.FirstName || ' ' || e.LastName AS support_rep, "
                 "COUNT(c.CustomerId) AS customers "
                 "FROM employees e JOIN customers c ON c.SupportRepId = e.EmployeeId "
                 "GROUP BY e.EmployeeId ORDER BY customers DESC;"),
     "ref_rows": [("Jane Peacock", 21), ("Margaret Park", 20), ("Steve Johnson", 18)],
     "ref_answer_keypoints": ["Jane Peacock", "21", "Steve Johnson", "18"],
     "tags": ["join", "group"]},

    {"id": "G8", "question": "哪个客户的累计消费金额最高？", "scope": "in",
     "tables": ["customers", "invoices"],
     "ref_sql": ("SELECT c.FirstName || ' ' || c.LastName AS customer, "
                 "SUM(i.Total) AS total_spent "
                 "FROM customers c JOIN invoices i ON i.CustomerId = c.CustomerId "
                 "GROUP BY c.CustomerId ORDER BY total_spent DESC LIMIT 1;"),
     "ref_rows": [("Helena Holý", 49.62)],
     "ref_answer_keypoints": ["Helena Holý", "49.62"], "tags": ["join", "topk"]},

    {"id": "G9", "question": "2010 年的总销售额是多少？", "scope": "in",
     "tables": ["invoices"],
     "ref_sql": ("SELECT SUM(Total) AS total_sales FROM invoices "
                 "WHERE InvoiceDate >= '2010-01-01' AND InvoiceDate < '2011-01-01';"),
     "ref_rows": [(481.45,)],
     "ref_answer_keypoints": ["481.45"], "tags": ["filter", "aggregate"]},

    # ---- 闸门样本：不进入流水线，只校验 [B] 范围判定 ----
    {"id": "N1", "question": "明天北京的天气怎么样？", "scope": "out",
     "tables": [], "ref_sql": None, "ref_rows": None,
     "ref_answer_keypoints": [], "tags": ["gate"]},
    {"id": "N2", "question": "帮我写一首关于夏天的诗", "scope": "out",
     "tables": [], "ref_sql": None, "ref_rows": None,
     "ref_answer_keypoints": [], "tags": ["gate"]},
    {"id": "N3", "question": "有多少？", "scope": "ambiguous",
     "tables": [], "ref_sql": None, "ref_rows": None,
     "ref_answer_keypoints": [], "tags": ["gate"]},
]


# --------------------------------------------------------------------------- #
# 结构探针集（5 题）：探查「非平凡 SQL 结构」下逐层裁判的表现
# --------------------------------------------------------------------------- #

# 为什么需要它：GOLDEN_SET 的 9 道 in 题里有 4 道是单表 `COUNT(*)`，生成的 SQL 与权威
# 几乎逐字相同 —— 这掩盖了 **L2（SQL 语义等价）在复杂结构上的脆弱性**。下面 5 题各针对
# 一种 GOLDEN_SET 未覆盖的结构，用来把那个盲区照亮。
#
# 用法与 GOLDEN_SET 不同：**它是探针，不是硬门槛**。L2 的判据是「与权威 SQL 是否**写法**
# 等价」（表连接方式 / 有无 ORDER BY / 用了哪个函数 / 选了哪几列 / 别名），而不是「结果是否
# 等价」；因此这 5 题的 L2 大概率亮灯 —— **这本身就是结论**，定位与解读见 README 的
# 「L2 的边界」一节。L4（结果等价）才是成败判决，这 5 题在 L4 上都是通过的。
STRUCTURE_PROBES = [
    {"id": "P1", "question": "曲目数量超过 100 首的流派有哪些，分别有多少首？",
     "scope": "in", "tables": ["tracks", "genres"],
     "ref_sql": ("SELECT g.Name AS genre, COUNT(*) AS n_tracks FROM tracks t "
                 "JOIN genres g ON t.GenreId = g.GenreId GROUP BY g.GenreId, g.Name "
                 "HAVING COUNT(*) > 100 ORDER BY n_tracks DESC;"),
     "ref_rows": None, "ref_answer_keypoints": ["Rock", "1297", "Jazz", "130"],
     "tags": ["group", "having"]},

    {"id": "P2", "question": "哪些曲目的时长比全库平均时长还长？请列出最长的 5 首。",
     "scope": "in", "tables": ["tracks"],
     "ref_sql": ("SELECT Name, Milliseconds FROM tracks "
                 "WHERE Milliseconds > (SELECT AVG(Milliseconds) FROM tracks) "
                 "ORDER BY Milliseconds DESC LIMIT 5;"),
     "ref_rows": None, "ref_answer_keypoints": ["Occupation / Precipice", "5286953"],
     "tags": ["subquery", "topk"]},

    {"id": "P3", "question": "每位员工的直属上司分别是谁？",
     "scope": "in", "tables": ["employees"],
     "ref_sql": ("SELECT e.FirstName || ' ' || e.LastName AS employee, "
                 "m.FirstName || ' ' || m.LastName AS manager FROM employees e "
                 "LEFT JOIN employees m ON e.ReportsTo = m.EmployeeId ORDER BY e.EmployeeId;"),
     "ref_rows": None,
     "ref_answer_keypoints": ["Nancy Edwards", "Jane Peacock", "Andrew Adams"],
     "tags": ["self_join", "left_join"]},

    {"id": "P4", "question": "按年份统计每年的销售总额，从早到晚列出。",
     "scope": "in", "tables": ["invoices"],
     "ref_sql": ("SELECT strftime('%Y', InvoiceDate) AS yr, ROUND(SUM(Total), 2) AS total_sales "
                 "FROM invoices GROUP BY yr ORDER BY yr;"),
     "ref_rows": None,
     "ref_answer_keypoints": ["2009", "449.46", "2010", "481.45"],
     "tags": ["date_bucket", "group"]},

    {"id": "P5", "question": "销售额最高的 3 位客户分别是谁，各排第几？",
     "scope": "in", "tables": ["customers", "invoices"],
     "ref_sql": ("WITH ranked AS (SELECT c.CustomerId, c.FirstName || ' ' || c.LastName AS customer, "
                 "SUM(i.Total) AS total_spent, "
                 "ROW_NUMBER() OVER (ORDER BY SUM(i.Total) DESC, c.CustomerId) AS rn "
                 "FROM customers c JOIN invoices i ON i.CustomerId = c.CustomerId "
                 "GROUP BY c.CustomerId) "
                 "SELECT rn, customer, ROUND(total_spent, 2) AS total_spent FROM ranked WHERE rn <= 3;"),
     "ref_rows": None,
     "ref_answer_keypoints": ["Helena Holý", "49.62", "Richard Cunningham"],
     "tags": ["cte", "window"]},
]


def _sqlite_path(db_url: str) -> str:
    """从 sqlite 连接串里取文件路径。"""
    if not db_url.startswith("sqlite"):
        raise ValueError("freeze_golden 只在 sqlite 上做双引擎交叉验证")
    path = db_url.split("sqlite:///", 1)[-1]
    return path


def freeze_golden(golden=None, parser=None):
    """重建并固化 ref_rows：双引擎交叉验证，不一致立即报错。

    只在需要重建验证集时手动调用；日常评测只读 `GOLDEN_SET` 里已冻结的值。
    """
    import sqlite3

    golden = golden if golden is not None else GOLDEN_SET
    parser = parser or DBParser(get_db_url(), verbose=False)
    db_file = _sqlite_path(parser.db_url) if parser.db_url.startswith("sqlite") else str(find_up("chinook.db"))
    con = sqlite3.connect(db_file)
    try:
        for it in golden:
            if it["scope"] != "in":
                continue
            _, rows_a = parser.execute_sql(it["ref_sql"])       # 引擎 A：SQLAlchemy
            rows_b = con.execute(it["ref_sql"]).fetchall()      # 引擎 B：原生 sqlite3
            if sorted(map(str, rows_a)) != sorted(map(str, rows_b)):
                raise AssertionError(f"{it['id']} 双引擎结果不一致：{rows_a} vs {rows_b}")
            it["ref_rows"] = rows_a
    finally:
        con.close()
    return golden


def scope_counts(golden=None) -> dict:
    """统计验证集里各 scope 的题量。"""
    golden = golden if golden is not None else GOLDEN_SET
    return {s: sum(x["scope"] == s for x in golden) for s in ("in", "out", "ambiguous")}


def freeze_probes(parser=None):
    """重建并固化 `STRUCTURE_PROBES` 的 ref_rows（同样走双引擎交叉验证）。

    与 `freeze_golden` 是同一套机制，只是换了数据集：探针集也是「答案独立于答题模型」的。
    """
    return freeze_golden(golden=STRUCTURE_PROBES, parser=parser)


# --------------------------------------------------------------------------- #
# 问题模拟生成器
# --------------------------------------------------------------------------- #

EVAL_TABLES = ["albums", "tracks", "invoices", "customers", "employees"]

_STYLES = {
    "count": "答案是单个数字的计数 / 统计类问题",
    "group": "答案是一组类别及对应数值的分组对比类问题",
}


def values_match(a, b, tol: float = 1e-6) -> bool:
    """数值容差比较，非数值退化为字符串比较（照搬原实现）。"""
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


class QuestionSimulator:
    """问题模拟生成器：从数据库内容合成用户提问（含参考 SQL 与真值）。"""

    def __init__(self, parser: DBParser):
        self.parser = parser

    def simulate_question(self, table: str, style: str = "count", user_style: bool = False) -> dict:
        """对单张表合成 1 个问题，并给出参考 SQL 与真值（真值可能为 None）。"""
        parser = self.parser
        schema = parser.get_table_fields(table).to_markdown()
        sample = parser.get_table_sample(table).to_markdown(index=False)
        fields = ", ".join(parser.get_table_fields(table).index)

        question = llm.strip_code_fence(llm.invoke_messages(
            prompts.SIMULATE_QUESTION_PROMPT.format_messages(
                table=table, fields=fields, sample=sample, schema=schema,
                style_desc=_STYLES.get(style, _STYLES["count"]))))
        if user_style:
            question = llm.strip_code_fence(llm.invoke_messages(
                prompts.SIMULATE_REWRITE_PROMPT.format_messages(question=question, table=table)))

        sql = llm.strip_code_fence(llm.invoke_messages(
            prompts.SIMULATE_SQL_PROMPT.format_messages(
                table=table, fields=fields, sample=sample, schema=schema, question=question)))

        ref_value = None
        if (is_readonly(sql)[0]
                and not re.search(r"\b(group\s+by|having)\b", sql, re.IGNORECASE)
                and parser.check_sql(sql)[0]):
            _, rows = parser.execute_sql(sql)
            if len(rows) == 1 and len(rows[0]) == 1:
                ref_value = rows[0][0]

        return {"table": table, "style": style, "question": question,
                "ref_sql": sql, "ref_value": ref_value}

    def simulate_questions(self, tables=None, n_per_table: int = 1, style: str = "count",
                           user_style: bool = False, max_try: int = 4) -> list:
        """批量合成问题；只保留「可执行且答案为单值」的题（便于自动判分）。"""
        tables = tables or self.parser.table_names
        items = []
        for t in tables:
            got = 0
            for _ in range(max_try):
                if got >= n_per_table:
                    break
                try:
                    rec = self.simulate_question(t, style=style, user_style=user_style)
                except Exception:  # noqa: BLE001
                    continue
                if rec["ref_value"] is None:
                    continue
                got += 1
                items.append(rec)
        return items

    def simulate_out_of_scope(self, n: int = 3) -> list:
        """负样本：与 chinook 音乐商店数据库完全无关的问题，用于测试范围判定。"""
        msgs = prompts.SIMULATE_OUT_OF_SCOPE_PROMPT.format_messages(n=n)
        raw = llm.strip_code_fence(llm.invoke_messages(msgs))
        import json
        try:
            qs = json.loads(raw).get("questions", [])
        except json.JSONDecodeError:
            qs = [ln.strip("- 0123456789.").strip() for ln in raw.splitlines() if ln.strip()]
        return [q for q in qs if q][:n]


# ---- 便捷函数（薄封装，保持原实现的可调用形态） ---- #

def simulate_question(parser: DBParser, table: str, style: str = "count", user_style: bool = False) -> dict:
    return QuestionSimulator(parser).simulate_question(table, style=style, user_style=user_style)


def simulate_questions(parser: DBParser, tables=None, n_per_table: int = 1, style: str = "count",
                       user_style: bool = False, max_try: int = 4) -> list:
    return QuestionSimulator(parser).simulate_questions(
        tables=tables, n_per_table=n_per_table, style=style, user_style=user_style, max_try=max_try)


def simulate_out_of_scope(n: int = 3) -> list:
    """负样本生成不依赖具体 parser，直接调用模块级提示词。"""
    import json
    msgs = prompts.SIMULATE_OUT_OF_SCOPE_PROMPT.format_messages(n=n)
    raw = llm.strip_code_fence(llm.invoke_messages(msgs))
    try:
        qs = json.loads(raw).get("questions", [])
    except json.JSONDecodeError:
        qs = [ln.strip("- 0123456789.").strip() for ln in raw.splitlines() if ln.strip()]
    return [q for q in qs if q][:n]


def build_eval_set(parser: DBParser, tables=None) -> list:
    """评测集 = 单表单值统计题（问题模拟生成器的特化调用）。"""
    return QuestionSimulator(parser).simulate_questions(
        tables=tables or EVAL_TABLES, n_per_table=1, style="count")
