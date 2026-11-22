"""共享工具层：schema 上下文拼装 + LangChain `@tool`。

**这一层是两条实现路径（`workflow/` 与 `agents/`）的唯一入口** —— 两条路都必须通过
这里的函数拿表结构、执行 SQL，不得各自直连 `DBParser`。这样做的收益：
「换库、改守卫、加审计」都只改一处，两条路自动同步。

| 原实现（`sql-agent-workflow.ipynb`） | 参考实现（`sql-agent.ipynb`） | 本实现 |
|---|---|---|
| `table_catalog()` / `schema_context()` | `@function_tool parse_table` | 同名函数 + `build_tools()` |
| —— | 「执行 sql」散落在 cell 里 | `run_readonly_sql()` 工具（内含 `is_readonly()` 守卫） |

`@tool` 是 LangChain 把**普通 Python 函数**暴露给 Agent 的标准方式：装饰器从函数签名与
docstring 自动生成工具描述（schema），模型据此决定何时调用。原参考实现用
`openai-agents` 的 `@function_tool`，此处换成 LangChain 的 `@tool`，**能力等价**。
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from .db import DBParser
from .guard import is_readonly

# D11：单次查询最多**回给模型**的行数（超出截断，并在结果文本里显式标注）。
# 作用面刻意收窄：只改 `run_readonly_sql()` 返回的那段文本，不动 `trace.rows` 的结构与语义
# （`workflow/graph.py` 的 `execute` 节点直连 `parser.execute_sql`，两条路各自保留自己的口径）。
MAX_ROWS = 1000


def table_catalog(parser: DBParser) -> str:
    """给 LLM 的「表清单 + 字段」（照搬原实现）。"""
    lines = []
    for t in parser.table_names:
        cols = list(parser.get_table_fields(t).index)
        lines.append(f"- {t}（字段：{', '.join(cols)}）")
    return "\n".join(lines)


def schema_context(tables: list, parser: DBParser) -> str:
    """把选中表的字段、样例与外键拼成上下文（照搬原实现）。"""
    parts = []
    for t in tables:
        parts.append(
            f"### 表 {t}\n字段：\n{parser.get_table_fields(t).to_markdown()}\n"
            f"样例：\n{parser.get_table_sample(t).to_markdown(index=False)}"
        )
    rels = parser.get_data_relations()
    if len(rels):
        rels = rels[rels["constrained_table"].isin(tables) & rels["referred_table"].isin(tables)]
        if len(rels):
            parts.append(f"### 选中表之间的外键\n{rels.to_string(index=False)}")
    return "\n\n".join(parts)


def run_readonly_sql(parser: DBParser, sql: str) -> str:
    """执行一条只读 SQL，返回可直接喂给模型的文本结果。

    这是**唯一**允许执行模型产出 SQL 的入口：先过 `is_readonly()` 结构性守卫，
    再试跑、再执行。原参考实现把这几步散在 notebook cell 里，这里收成一个具名函数，
    既供 `@tool` 复用，也供两条路径直接调用。

    返回文本超过 `MAX_ROWS` 行时**截断并标注**（D11）：前缀仍是 `columns=...\nrows=...`，
    只在末尾多一行说明，故既有调用方的解析方式不受影响。
    """
    ok, msg = is_readonly(sql)
    if not ok:
        return f"拒绝执行（只读守卫）：{msg}"
    ok, err = parser.check_sql(sql)
    if not ok:
        return f"SQL 执行失败：{str(err).strip().splitlines()[-1]}"
    columns, rows = parser.execute_sql(sql)
    if len(rows) <= MAX_ROWS:
        return f"columns={columns}\nrows={rows}"
    return (f"columns={columns}\nrows={rows[:MAX_ROWS]}\n"
            f"（结果共 {len(rows)} 行，已截断为前 {MAX_ROWS} 行）")


def build_tools(parser: DBParser) -> list[BaseTool]:
    """为某个 `DBParser` 构建一组 LangChain 工具（供 `create_agent` 使用）。

    把 parser 通过闭包绑定到工具上 —— 工具工厂模式，避免全局变量。
    """

    @tool
    def list_tables() -> str:
        """列出数据库中所有可用的表名。"""
        return ", ".join(parser.table_names)

    @tool
    def parse_table(table_name: str) -> str:
        """Return fields of the given table.（返回给定表的字段结构，markdown 表格）"""
        if table_name not in parser.table_names:
            return f"未找到表 {table_name}；可选：{', '.join(parser.table_names)}"
        return parser.get_table_fields(table_name).to_markdown()

    @tool
    def run_sql(sql: str) -> str:
        """执行一条只读 SQL（只允许单条 SELECT / WITH）并返回结果。"""
        return run_readonly_sql(parser, sql)

    return [list_tables, parse_table, run_sql]
