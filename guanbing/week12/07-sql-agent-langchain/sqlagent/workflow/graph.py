"""LangGraph 显式状态图 —— 复刻原版 7 阶段确定性流水线。

原实现的 `run_workflow()` 是一个手写 `for` 循环 + `if` 分支；本实现把它**画成一张图**：
节点 = 阶段，边 = 流转，条件边 = 「校验失败该往哪走」。两种写法行为等价，但图的写法带来三个好处：

1. **流转可见**：`graph.get_graph().draw_mermaid()` 直接画出拓扑，评审不用读代码就能核对；
2. **可检查点**：接上 `checkpointer` 就能把中间状态落盘、支持断点续跑（原实现做不到）；
3. **可组合**：子图 / 人工介入（`interrupt_before`）都是同一套机制。

对照表（节点 ↔ 原函数）：

| 原实现 | 本实现节点 | 类型 |
|---|---|---|
| `select_tables()` | `select` | 一次 LLM（结构化输出） |
| `generate_sql()` | `generate` | 一次 LLM |
| `is_readonly()` + `check_sql()` | `check` | 纯代码（守卫 + 试跑） |
| `rewrite_sql()` | `rewrite` | 一次 LLM |
| `execute_sql()` | `execute` | 纯代码 |
| `summarize()` | `summarize` | 一次 LLM |
| `if ok or attempt == MAX_REWRITE: break` | `route_after_check()` 条件边 | 纯代码 |

> LangGraph 是「怎么流转」，LangChain 是「每一步怎么调模型」。两者不是二选一：
> LangChain v1 的 `create_agent`（见 `sqlagent/agents/`）内部**就是**用 LangGraph 实现的。
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .. import llm, prompts
from ..db import DBParser
from ..guard import is_readonly
from ..schemas import TableSelection
from ..tools import schema_context, table_catalog

# 与原实现一致：最多改写 3 次（校验总次数 = MAX_REWRITE + 1）
MAX_REWRITE = 3

FAIL_ANSWER = "抱歉，未能生成可执行的 SQL，已放弃（不编造结果）。"


class WorkflowState(TypedDict, total=False):
    """图的状态：与原 `trace` 字典一一对应。"""

    question: str
    tables: list
    sql: str
    attempts: int
    ok: bool
    error: str | None
    columns: list | None
    rows: list | None
    answer: str | None


# --------------------------------------------------------------------------- #
# 阶段函数（与原实现逐个等价）
# --------------------------------------------------------------------------- #

def select_tables(question: str, parser: DBParser) -> list:
    """Stage 2：结合用户提问，选出回答问题所需的最小表集合。

    与原实现的差异：原实现解析失败时用正则从原文里抽表名（需要拿到原始字符串），
    本实现改为「结构化输出 → 校验过的 `TableSelection`」，解析失败即落回首表兜底。
    """
    rels = parser.get_data_relations()
    rels_text = rels.to_string(index=False) if len(rels) else "无"
    msgs = prompts.SELECT_TABLES_PROMPT.format_messages(
        question=question, catalog=table_catalog(parser), relations=rels_text,
    )
    sel = llm.invoke_structured_messages(TableSelection, msgs)
    picked = sel.tables if sel is not None else []
    picked = [t for t in picked if t in parser.table_names]
    return picked or parser.table_names[:1]


def generate_sql(question: str, tables: list, parser: DBParser) -> str:
    """Stage 3：结合提问与选中表生成 SQL。"""
    msgs = prompts.GENERATE_SQL_PROMPT.format_messages(
        question=question, schema=schema_context(tables, parser),
    )
    return llm.strip_code_fence(llm.invoke_messages(msgs))


def rewrite_sql(question: str, sql: str, error: str, tables: list, parser: DBParser) -> str:
    """Stage 5：把「报错 + 原 SQL + 问题 + schema」回灌 LLM 修正。"""
    msgs = prompts.REWRITE_SQL_PROMPT.format_messages(
        question=question, schema=schema_context(tables, parser), sql=sql, error=error,
    )
    return llm.strip_code_fence(llm.invoke_messages(msgs))


def summarize(question: str, sql: str, rows: list, max_rows: int = 50) -> str:
    """Stage 7：把「问题 + SQL + 结果」组织为自然语言结论。"""
    msgs = prompts.SUMMARIZE_PROMPT.format_messages(
        question=question, sql=sql, total=len(rows), max_rows=max_rows, rows=rows[:max_rows],
    )
    return llm.invoke_messages(msgs)


# --------------------------------------------------------------------------- #
# 构图
# --------------------------------------------------------------------------- #

def build_graph(parser: DBParser, *, verbose: bool = True) -> Any:
    """构建并编译状态图。返回的 `CompiledStateGraph` 可直接 `.invoke(state)`。"""

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    def node_select(state: WorkflowState) -> dict:
        tables = select_tables(state["question"], parser)
        log(f"[2] 选中表：{tables}")
        return {"tables": tables}

    def node_generate(state: WorkflowState) -> dict:
        sql = generate_sql(state["question"], state["tables"], parser)
        log(f"[3] 生成 SQL：{sql}")
        return {"sql": sql}

    def node_check(state: WorkflowState) -> dict:
        """Stage 4：只读守卫 + 试跑；记录本次是第几次校验。"""
        sql = state["sql"]
        attempt = state.get("attempts", 0) + 1

        ro_ok, ro_msg = is_readonly(sql)
        if not ro_ok:
            ok, err = False, ro_msg
        else:
            ok, err = parser.check_sql(sql)

        detail = "通过" if ok else "失败 → " + str(err).strip().splitlines()[-1]
        log(f"[4] 校验（第 {attempt} 次）：{detail}")
        return {"attempts": attempt, "ok": ok, "error": None if ok else err}

    def route_after_check(state: WorkflowState) -> str:
        """条件边：通过 → 执行；未通过且次数未用尽 → 改写；否则放弃。"""
        if state.get("ok"):
            return "execute"
        if state.get("attempts", 0) <= MAX_REWRITE:
            return "rewrite"
        return "fail"

    def node_rewrite(state: WorkflowState) -> dict:
        sql = rewrite_sql(state["question"], state["sql"], state["error"], state["tables"], parser)
        log(f"[5] 改写 SQL：{sql}")
        return {"sql": sql}

    def node_execute(state: WorkflowState) -> dict:
        columns, rows = parser.execute_sql(state["sql"])
        log(f"[6] 执行结果：{len(rows)} 行")
        return {"columns": columns, "rows": rows}

    def node_summarize(state: WorkflowState) -> dict:
        answer = summarize(state["question"], state["sql"], state["rows"])
        log(f"[7] 汇总回答：{answer}")
        return {"answer": answer}

    def node_fail(state: WorkflowState) -> dict:
        return {"answer": FAIL_ANSWER}

    g = StateGraph(WorkflowState)
    g.add_node("select", node_select)
    g.add_node("generate", node_generate)
    g.add_node("check", node_check)
    g.add_node("rewrite", node_rewrite)
    g.add_node("execute", node_execute)
    g.add_node("summarize", node_summarize)
    g.add_node("fail", node_fail)

    g.add_edge(START, "select")
    g.add_edge("select", "generate")
    g.add_edge("generate", "check")
    g.add_conditional_edges("check", route_after_check,
                            {"execute": "execute", "rewrite": "rewrite", "fail": "fail"})
    g.add_edge("rewrite", "check")          # ← 唯一的回环
    g.add_edge("execute", "summarize")
    g.add_edge("summarize", END)
    g.add_edge("fail", END)

    return g.compile()


def run_workflow(question: str, parser: DBParser, verbose: bool = True) -> dict:
    """执行完整 SQL Agent 工作流，返回带轨迹的结果字典（签名/字段同原实现）。"""
    graph = build_graph(parser, verbose=verbose)
    final = graph.invoke({
        "question": question, "attempts": 0, "ok": False, "error": None,
        "columns": None, "rows": None, "answer": None,
    })
    return {
        "question": question,
        "tables": final.get("tables"),
        "sql": final.get("sql"),
        "attempts": final.get("attempts", 0),
        "ok": bool(final.get("ok")),
        "error": final.get("error"),
        "columns": final.get("columns"),
        "rows": final.get("rows"),
        "answer": final.get("answer"),
    }
