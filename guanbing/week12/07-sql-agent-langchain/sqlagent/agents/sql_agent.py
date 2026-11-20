"""LangChain `create_agent` 路径 —— 复刻参考实现 `sql-agent.ipynb` 的三角色 Agent。

参考 notebook 里画了三个角色（`SQLTableSelectAgent` = "SQL Planer" / `SQLGeneratorAgent` /
`SQLResultSummaryAgent`），但后两个是空壳。本模块把它们**补齐成真正可跑的 Agent**，
并额外给出一条「单 Agent + 工具」的自主路线。

| 参考实现（openai-agents SDK） | 本实现（LangChain） |
|---|---|
| `@function_tool parse_table` | `sqlagent.tools.build_tools()` 里的 `parse_table` |
| `Agent(name="SQL Planer", instructions=..., tools=[parse_table])` | `create_agent(model, tools, system_prompt=...)` |
| `Runner.run(agent, input=...)` | `agent.invoke({"messages": [...]})` |
| `SQLGeneratorAgent` / `SQLResultSummaryAgent`（空壳） | 补齐为 generator / summarizer 两个 Agent |

**为什么两者共存**：`workflow/` 是「编排写在 Python 里、每步一次调用」的确定性路线；
`agents/` 是「把决策权交给模型」的自主路线。它们**共用同一套内核**（`db` / `guard` / `tools`），
所以不是重复造轮子，而是同一内核的两种驱动方式 —— 这正是原设计文档里
「不是所有东西都该 LangChain 化」的另一面：能用就复用，不该包的绝不硬包。
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent

from .. import llm
from ..db import DBParser
from ..guard import is_readonly
from ..tools import build_tools, schema_context, table_catalog
from ..workflow.graph import FAIL_ANSWER, MAX_REWRITE

# --------------------------------------------------------------------------- #
# 角色提示词
# --------------------------------------------------------------------------- #

PLANNER_PROMPT = (
    "你是一个专业的 SQL 专家。你的任务是：分析用户提问，从候选表里挑出回答该问题所需的"
    "最小表集合。你可以调用 parse_table 查看某张表的字段。"
    "最终只输出 JSON：{\"tables\": [\"表名\", ...]}，不要输出其他内容。"
)

GENERATOR_PROMPT = (
    "你是一个专业的 SQLite 专家。根据用户提问与给定的表结构，只生成一条只读 SELECT 语句。"
    "不要解释，不要写多条语句。最终只输出 SQL（可带分号）。"
)

REWRITER_PROMPT = (
    "你是一个专业的 SQLite 专家。上一次的 SQL 执行失败，请根据报错修正。"
    "只输出修正后的单条只读 SELECT，不要解释。"
)

SUMMARIZER_PROMPT = (
    "你是一个数据分析助手。请依据 SQL 执行结果，用简洁自然语言回答用户问题，不要编造数据。"
)

# 单 Agent 自主路线的系统提示词
AUTONOMOUS_PROMPT = (
    "你是一个 SQL 数据分析助手，面向 chinook 音乐商店数据库。"
    "你可以调用 list_tables 看表清单、parse_table 看字段结构、run_sql 执行只读查询。"
    "请先想清楚需要哪些表，再生成并执行 SQL，最后用简洁的中文给出结论。"
    "只能生成只读查询；如果问题与数据库无关，直接说明无法回答，不要编造数据。"
)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _last_ai_text(result: Any) -> str:
    """从 Agent 返回状态里取出最后一段非空 AI 文本。"""
    messages = result.get("messages", result) if isinstance(result, dict) else result
    for m in reversed(list(messages)):
        if getattr(m, "type", None) == "ai":
            text = llm.content_to_text(getattr(m, "content", "")).strip()
            if text:
                return text
    return ""


def _parse_tables(raw: str, parser: DBParser) -> list:
    """从模型输出里解析表名列表（容错：JSON 优先，退化为抽取标识符）。"""
    text = llm.strip_code_fence(raw)
    try:
        picked = json.loads(text).get("tables", [])
    except (json.JSONDecodeError, AttributeError):
        import re
        picked = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text)
    picked = [t for t in picked if t in parser.table_names]
    return picked or parser.table_names[:1]


# --------------------------------------------------------------------------- #
# 三角色 Agent
# --------------------------------------------------------------------------- #

class SQLAgent:
    """三角色 Agent 编排（planner → generator → 守卫/改写闭环 → summarizer）。

    对外暴露 `run()` / `ask()`，返回结构与 `workflow.run_workflow` / `ChatBI.ask` 对齐，
    便于两条路互换与统一评测。
    """

    def __init__(self, parser: DBParser, *, max_rewrite: int = MAX_REWRITE,
                 verbose: bool = True, model=None):
        self.parser = parser
        self.max_rewrite = max_rewrite
        self.verbose = verbose
        self.model = model or llm.build_raw_model()

        tools = build_tools(parser)
        planner_tools = [t for t in tools if t.name in ("list_tables", "parse_table")]
        self.planner = create_agent(self.model, planner_tools, system_prompt=PLANNER_PROMPT)
        self.generator = create_agent(self.model, [], system_prompt=GENERATOR_PROMPT)
        self.rewriter = create_agent(self.model, [], system_prompt=REWRITER_PROMPT)
        self.summarizer = create_agent(self.model, [], system_prompt=SUMMARIZER_PROMPT)

    # ---- 各角色单步（供测试与调试） ----

    def select_tables(self, question: str) -> list:
        prompt = (f"用户提问：{question}\n\n候选表：\n{table_catalog(self.parser)}\n\n"
                  f"表间外键关系：\n"
                  f"{self.parser.get_data_relations().to_string(index=False) or '无'}\n\n"
                  f"请只输出 JSON：{{\"tables\": [\"表名\", ...]}}")
        result = self.planner.invoke({"messages": [{"role": "user", "content": prompt}]})
        return _parse_tables(_last_ai_text(result), self.parser)

    def generate_sql(self, question: str, tables: list) -> str:
        prompt = f"用户问题：{question}\n\n相关表信息：\n{schema_context(tables, self.parser)}\n\n请只输出 SQL："
        result = self.generator.invoke({"messages": [{"role": "user", "content": prompt}]})
        return llm.strip_code_fence(_last_ai_text(result))

    def rewrite_sql(self, question: str, sql: str, error: str, tables: list) -> str:
        prompt = (f"用户问题：{question}\n\n相关表信息：\n{schema_context(tables, self.parser)}\n\n"
                  f"上一次 SQL：\n{sql}\n\n报错信息：\n{error}\n\n请输出修正后的 SQL：")
        result = self.rewriter.invoke({"messages": [{"role": "user", "content": prompt}]})
        return llm.strip_code_fence(_last_ai_text(result))

    def summarize(self, question: str, sql: str, rows: list, max_rows: int = 50) -> str:
        prompt = (f"用户问题：{question}\n\n执行的 SQL：\n{sql}\n\n"
                  f"执行结果（共 {len(rows)} 行，最多展示 {max_rows} 行）：\n{rows[:max_rows]}\n\n"
                  f"请给出自然语言结论：")
        result = self.summarizer.invoke({"messages": [{"role": "user", "content": prompt}]})
        return _last_ai_text(result)

    # ---- 编排（与 workflow.run_workflow 同构） ----

    def run(self, question: str) -> dict:
        trace = {"question": question, "tables": None, "sql": None, "attempts": 0,
                 "ok": False, "error": None, "columns": None, "rows": None, "answer": None}

        def log(msg):
            if self.verbose:
                print(msg)

        tables = self.select_tables(question)
        trace["tables"] = tables
        log(f"[2] 选中表：{tables}")

        sql = self.generate_sql(question, tables)
        log(f"[3] 生成 SQL：{sql}")

        ok, err = False, None
        for attempt in range(self.max_rewrite + 1):
            ro_ok, ro_msg = is_readonly(sql)
            if not ro_ok:
                ok, err = False, ro_msg
            else:
                ok, err = self.parser.check_sql(sql)
            trace["attempts"] = attempt + 1
            detail = "通过" if ok else "失败 → " + str(err).strip().splitlines()[-1]
            log(f"[4] 校验（第 {attempt + 1} 次）：{detail}")
            if ok or attempt == self.max_rewrite:
                break
            sql = self.rewrite_sql(question, sql, str(err), tables)
            log(f"[5] 改写 SQL：{sql}")

        trace["sql"] = sql
        trace["ok"] = ok
        trace["error"] = None if ok else err
        if not ok:
            trace["answer"] = FAIL_ANSWER
            return trace

        columns, rows = self.parser.execute_sql(sql)
        trace["columns"] = columns
        trace["rows"] = rows
        log(f"[6] 执行结果：{len(rows)} 行")

        trace["answer"] = self.summarize(question, sql, rows)
        log(f"[7] 汇总回答：{trace['answer']}")
        return trace

    def ask(self, question: str, show_trace: bool = False) -> dict:
        """与 `ChatBI.ask` 同形的入口（含 [B] 范围闸门），便于统一评测与互换。"""
        from ..chatbi import OUT_OF_SCOPE_TEMPLATE, scope_check

        trace = {"raw_question": question, "standalone_question": question, "scope": None,
                 "tables": None, "sql": None, "attempts": 0, "ok": False,
                 "columns": None, "rows": None, "answer": None}

        scope = scope_check(question, self.parser)
        trace["scope"] = scope
        if show_trace:
            print(f"[B] 范围判定：{scope['scope']}（{scope['reason']}）")

        if scope["scope"] == "out":
            trace["answer"] = OUT_OF_SCOPE_TEMPLATE.format(reason=scope["reason"])
        elif scope["scope"] == "ambiguous":
            trace["answer"] = f"我需要先澄清一下：{scope['clarify'] or '能再具体一点吗？'}"
        else:
            prev = self.verbose
            self.verbose = show_trace
            res = self.run(question)
            self.verbose = prev
            for k in ("tables", "sql", "attempts", "ok", "columns", "rows", "answer"):
                trace[k] = res[k]
        return trace


# --------------------------------------------------------------------------- #
# 单 Agent 自主路线
# --------------------------------------------------------------------------- #

def build_autonomous_agent(parser: DBParser, *, model=None):
    """单 Agent + 工具（list_tables / parse_table / run_sql）：由模型自主决定调用顺序。"""
    return create_agent(
        model or llm.build_raw_model(),
        build_tools(parser),
        system_prompt=AUTONOMOUS_PROMPT,
    )


def run_autonomous(question: str, parser: DBParser, *, model=None) -> dict:
    """跑一次自主 Agent，返回 {answer, messages}（messages 是完整轨迹，可打印观察）。"""
    agent = build_autonomous_agent(parser, model=model)
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return {"answer": _last_ai_text(result), "messages": result.get("messages", [])}
