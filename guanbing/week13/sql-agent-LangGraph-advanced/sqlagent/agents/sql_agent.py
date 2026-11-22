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
import re
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ModelFallbackMiddleware
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .. import i18n, llm
from ..config import get_fallback_settings
from ..db import DBParser
from ..guard import is_readonly
from ..tools import build_tools, schema_context, table_catalog
from ..workflow.graph import MAX_REWRITE

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

# 英文变体：只改「结论用哪种语言表述」，不改角色行为（见 README「中英双语」一节）。
SUMMARIZER_PROMPT_EN = (
    "You are a data analysis assistant. Based on the SQL result, answer the user's question "
    "in concise natural language. Do not make up data."
)

# 单 Agent 自主路线的系统提示词
AUTONOMOUS_PROMPT = (
    "你是一个 SQL 数据分析助手，面向 chinook 音乐商店数据库。"
    "先调用 write_sql 起草一条只读 SELECT（它会自己去查表结构，你**不必**把表结构贴给它），"
    "拿到它返回的 sql 后用 run_sql 执行，最后用简洁的中文给出结论。"
    "你也可以用 list_tables / parse_table 自己看结构。"
    "只能生成只读查询；如果问题与数据库无关，直接说明无法回答，不要编造数据。"
)

AUTONOMOUS_PROMPT_EN = (
    "You are a SQL data analysis assistant for the chinook music store database. "
    "First call write_sql to draft a read-only SELECT (it looks up the schema itself — you do "
    "NOT need to paste the schema to it); once it returns the sql, run it with run_sql, and "
    "finally give a concise conclusion in English. "
    "You may also use list_tables / parse_table to inspect the structure yourself. "
    "Only produce read-only queries; if the question is unrelated to the database, just say you "
    "cannot answer it — do not make up data."
)


def _pick(zh: str, en: str, lang: str) -> str:
    """按语言挑一条文案（`en` 走英文，其余回落中文 —— 与 `i18n.DEFAULT_LANG` 一致）。"""
    return en if lang == "en" else zh


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
                 verbose: bool = True, model=None, lang: str = "zh"):
        self.parser = parser
        self.max_rewrite = max_rewrite
        self.verbose = verbose
        self.lang = lang
        self.model = model or llm.build_raw_model()

        tools = build_tools(parser)
        planner_tools = [t for t in tools if t.name in ("list_tables", "parse_table")]
        self.planner = create_agent(self.model, planner_tools, system_prompt=PLANNER_PROMPT)
        self.generator = create_agent(self.model, [], system_prompt=GENERATOR_PROMPT)
        self.rewriter = create_agent(self.model, [], system_prompt=REWRITER_PROMPT)
        self.summarizer = create_agent(self.model, [],
                                       system_prompt=_pick(SUMMARIZER_PROMPT,
                                                           SUMMARIZER_PROMPT_EN, lang))

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
        if self.lang == "en":
            prompt = (f"User question: {question}\n\nExecuted SQL:\n{sql}\n\n"
                      f"Result ({len(rows)} rows in total, showing up to {max_rows}):\n"
                      f"{rows[:max_rows]}\n\nGive a natural-language conclusion:")
        else:
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
            trace["answer"] = i18n.text("fail_answer", self.lang)
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
        from ..chatbi import scope_check

        trace = {"raw_question": question, "standalone_question": question, "scope": None,
                 "tables": None, "sql": None, "attempts": 0, "ok": False,
                 "columns": None, "rows": None, "answer": None}

        scope = scope_check(question, self.parser, lang=self.lang)
        trace["scope"] = scope
        if show_trace:
            print(f"[B] 范围判定：{scope['scope']}（{scope['reason']}）")

        if scope["scope"] == "out":
            trace["answer"] = i18n.text("out_of_scope", self.lang).format(reason=scope["reason"])
        elif scope["scope"] == "ambiguous":
            clarify = scope["clarify"] or i18n.text("clarify_fallback", self.lang)
            trace["answer"] = i18n.text("need_clarify_prefix", self.lang) + clarify
        else:
            prev = self.verbose
            self.verbose = show_trace
            res = self.run(question)
            self.verbose = prev
            for k in ("tables", "sql", "attempts", "ok", "columns", "rows", "answer"):
                trace[k] = res[k]
        return trace


# --------------------------------------------------------------------------- #
# SQL 生成子代理（批 5，D21 / D22 / D23）
# --------------------------------------------------------------------------- #

DRAFT_SQL_PROMPT = (
    "你是一个专业的 SQLite 专家。请根据用户提问生成**一条**只读 SELECT 语句。"
    "你可以调用 list_tables 看表清单、parse_table 看字段结构 —— 先把要用的表结构看清楚，"
    "再输出 SQL。只输出 SQL 本身（可带分号），不要解释，不要写多条语句。"
)


class SqlDraft(BaseModel):
    """子代理交给父图的**唯一**产物（D23）。

    父 context 里只留这一条摘要：schema 原文、试跑报错、每一版草稿，都只活在子代理
    自己的窗口里。`trace.sql` / `trace.attempts` 由它回填，形状不变。
    """

    sql: str
    tables: list[str] = Field(default_factory=list)
    attempts: int = 1
    note: str = ""


def _tables_in_sql(sql: str, parser: DBParser) -> list:
    """从 SQL 文本里认出引用到的表（只作展示与审计，不参与执行正确性判断）。"""
    identifiers = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql))
    return [t for t in parser.table_names if t in identifiers]


def build_sql_subagent(parser: DBParser, *, model=None):
    """SQL 生成子代理（D22）：**只拿问题文本**，自己用 `list_tables` / `parse_table` 探索 schema。

    工具刻意只有这两个——**不给 `run_sql`**：子代理只负责「写出对的 SQL」，执行仍由父图
    经 `run_sql` 走人工闸门。也刻意**不挂 middleware**（D23）：`interrupt()` 只允许留在父图，
    否则「一个会话一个闸门」的交互契约会被子图里的暂停打乱。
    """
    tools = [t for t in build_tools(parser) if t.name in ("list_tables", "parse_table")]
    return create_agent(model or llm.build_raw_model(), tools, system_prompt=DRAFT_SQL_PROMPT)


def draft_sql(parser: DBParser, question: str, *, model=None,
              max_attempts: int = MAX_REWRITE + 1) -> SqlDraft:
    """在**干净窗口**里生成 SQL 并自纠，返回结构化摘要（D22 / D23）。

    自纠闭环整个发生在子代理自己的 `messages` 里：守卫/试跑不通过时，把报错作为下一条
    用户消息**回灌给子代理**，父图完全看不到这一串来回。多花的 1–2 次 LLM 调用，买的就是
    父上下文不被 schema 与报错撑大（D22 写明的代价）。

    - `attempts`：真正试跑过几次（含首次）；守卫 + `check_sql` 都过即返回；
    - `note`：最后一版的失败原因（成功则为空串），供前端与审计归因；
    - `tables`：草稿里引用到的表，供父图展示「统计口径」。
    """
    agent = build_sql_subagent(parser, model=model)
    messages: list = [{"role": "user", "content": question}]
    sql, note = "", ""
    for attempt in range(1, max_attempts + 1):
        result = agent.invoke({"messages": messages})
        messages = list(result.get("messages", []))
        sql = llm.strip_code_fence(_last_ai_text(result))
        ok, err = is_readonly(sql)
        if ok:
            ok, err = parser.check_sql(sql)
        if ok:
            return SqlDraft(sql=sql, tables=_tables_in_sql(sql, parser), attempts=attempt)
        note = str(err).strip().splitlines()[-1] if err else "生成的 SQL 未通过校验"
        messages.append({"role": "user",
                         "content": f"上一次的 SQL 未通过校验：{note}\n请据此修正，只输出 SQL。"})
    return SqlDraft(sql=sql, tables=_tables_in_sql(sql, parser), attempts=max_attempts, note=note)


def make_write_sql_tool(parser: DBParser, *, model=None) -> BaseTool:
    """把子代理包成一个工具交给父 agent（D21 / D23）。

    入参**只有问题文本**（D22）：父 agent 不需要、也不该把 schema 塞进来，否则「探索」
    就留在了父上下文里，疼点原封不动。返回值是摘要的 JSON 字符串——`@tool` 的产物必须
    可序列化，而父 context 里也就只多这一条摘要。
    """

    @tool
    def write_sql(question: str) -> str:
        """根据用户问题起草一条只读 SELECT（内部自己查表结构并自纠，不要传表结构）。
        返回 JSON：{"sql": ..., "tables": [...], "attempts": N, "note": ...}。
        拿到 sql 后请用 run_sql 执行它。"""
        return draft_sql(parser, question, model=model).model_dump_json()

    return write_sql


# --------------------------------------------------------------------------- #
# 单 Agent 自主路线
# --------------------------------------------------------------------------- #

def build_agent_middleware(*, human_review: bool = False) -> list:
    """装配 agent 路线的 middleware（可用性 + 安全性各一）。

    - `ModelFallbackMiddleware`：主模型调用失败时按序切到备用模型。配置来自 `.env` 的
      `LLM_FALLBACK_PROVIDER` / `LLM_FALLBACK_MODEL`（与主线 `llm.build_chat_model` 同源）。
      **为什么 agent 路线非它不可**：`create_agent` 要求真 `BaseChatModel`，而
      `.with_fallbacks()` 的产物是 `RunnableWithFallbacks`，喂不进去——这正是
      「middleware 是 agent 路线专属能力」的最直观证据。
    - `HumanInTheLoopMiddleware`：`run_sql` 执行前 `interrupt()` 等人工放行 / 改写 / 驳回。
      补上 `SQLAgent.run()` 直接执行、**无人工闸门**的缺口。
    """
    middleware: list = []
    fallback = get_fallback_settings()
    if fallback is not None:
        middleware.append(ModelFallbackMiddleware(llm.build_raw_model(fallback)))
    if human_review:
        middleware.append(HumanInTheLoopMiddleware({
            "run_sql": {"allowed_decisions": ["approve", "edit", "reject"]},
        }))
    return middleware


def build_autonomous_agent(parser: DBParser, *, model=None, middleware: list | None = None,
                           checkpointer=None, lang: str = "zh"):
    """单 Agent + 工具（`write_sql` / `list_tables` / `parse_table` / `run_sql`）：由模型自主决定调用顺序。

    批 5 起多了一个 `write_sql`（D21–D23）：SQL 的「起草 + 自纠」被挪进一个只拿问题文本的
    子代理，父上下文因此不再被 schema 原文与报错撑大；执行仍走父图的 `run_sql`（人工闸门不动）。

    `middleware` / `checkpointer` 透传给 `create_agent`：挂上 `HumanInTheLoopMiddleware`
    后必须同时给 `checkpointer`，图才能在 `interrupt()` 处暂停、并按 `thread_id` 续接。
    `lang` 决定系统提示词的语言（`en` 时最终结论用英文）。
    """
    model = model or llm.build_raw_model()
    tools = [*build_tools(parser), make_write_sql_tool(parser, model=model)]
    return create_agent(
        model,
        tools,
        system_prompt=_pick(AUTONOMOUS_PROMPT, AUTONOMOUS_PROMPT_EN, lang),
        # `create_agent` 会直接迭代 middleware，显式传 None 会炸 —— 空列表才是「不挂」的写法
        middleware=middleware or [],
        checkpointer=checkpointer,
    )


def run_autonomous(question: str, parser: DBParser, *, model=None, lang: str = "zh") -> dict:
    """跑一次自主 Agent，返回 {answer, messages}（messages 是完整轨迹，可打印观察）。"""
    agent = build_autonomous_agent(parser, model=model, lang=lang)
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return {"answer": _last_ai_text(result), "messages": result.get("messages", [])}
