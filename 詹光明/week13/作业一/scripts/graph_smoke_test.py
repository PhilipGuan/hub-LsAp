"""离线图流程测试：用假链替换 LLM，验证 LangGraph 的路由与循环。

不调用任何模型，也不需要 API key；但会读取真实的 ``data/chinook.db``。

用法：
    python scripts/graph_smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import sql_agent.graph as graph_module  # noqa: E402
from sql_agent.config import settings  # noqa: E402
from sql_agent.graph import SQLAgentGraph  # noqa: E402
from sql_agent.models import FinalAnswer, SQLDraft, TableSelection  # noqa: E402
from sql_agent.workflow import SQLAgentWorkflow  # noqa: E402

GOOD_SQL = "SELECT Name FROM Artist ORDER BY ArtistId LIMIT 3"
BAD_SQL = "SELECT Nope FROM Artist"


class FakeChain:
    """按顺序返回预设结果；可以抛出异常，也可以在调用次数不符时报错。"""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, _inputs: dict) -> Any:
        self.calls += 1
        if not self.responses:
            raise AssertionError(f"链被调用了 {self.calls} 次，超出预设")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _selection(tables: list[str]) -> TableSelection:
    return TableSelection(tables=tables, reason="测试选择", confidence=0.9)


def _draft(sql: str, *, cannot_fix: bool = False) -> SQLDraft:
    return SQLDraft(sql=sql, explanation="测试 SQL", cannot_fix=cannot_fix)


def _answer(text: str = "测试结论") -> FinalAnswer:
    return FinalAnswer(answer=text, evidence=["3 条记录"])


def _patch(chains: dict[str, FakeChain]) -> Callable[[], None]:
    """临时替换 graph 模块里的链工厂，返回恢复函数。"""

    names = {
        "select_tables": "get_table_selector_chain",
        "generate_sql": "get_sql_generator_chain",
        "rewrite_sql": "get_sql_rewriter_chain",
        "summarize": "get_result_summary_chain",
    }
    originals = {name: getattr(graph_module, name) for name in names.values()}
    for key, chain in chains.items():
        setattr(graph_module, names[key], lambda chain=chain: chain)

    def restore() -> None:
        for name, original in originals.items():
            setattr(graph_module, name, original)

    return restore


async def _run(
    chains: dict[str, FakeChain],
    *,
    max_repair_attempts: int | None = None,
    inspect_state: bool = False,
):
    restore = _patch(chains)
    try:
        builder = SQLAgentGraph(max_repair_attempts=max_repair_attempts)
        compiled = builder.build()
        question = "测试问题"
        if inspect_state:
            return await compiled.ainvoke(
                {"question": question, "trace": []},
                config={"recursion_limit": builder.recursion_limit},
            )
        workflow = SQLAgentWorkflow(
            compiled_graph=compiled, max_repair_attempts=max_repair_attempts
        )
        return await workflow.run(question)
    finally:
        restore()


def check_graph_shape() -> None:
    compiled = SQLAgentGraph().build()
    nodes = set(compiled.get_graph().nodes) - {"__start__", "__end__"}
    expected = {
        "select_tables",
        "generate_sql",
        "validate_execute",
        "rewrite_sql",
        "summarize",
        "finalize",
    }
    print(f"图节点：{sorted(nodes)}")
    assert nodes == expected, f"节点不符合预期：{nodes ^ expected}"


async def check_happy_path() -> None:
    state = await _run(
        {
            "select_tables": FakeChain([_selection(["Artist"])]),
            "generate_sql": FakeChain([_draft(GOOD_SQL)]),
            "summarize": FakeChain([_answer()]),
        },
        inspect_state=True,
    )
    output = state["output"]
    print(f"正常路径：rows={output.row_count} trace={state['trace']}")
    assert output.error is None
    assert output.row_count == 3
    assert output.repair_attempts == 0
    assert state["trace"][:3] == [
        "select_tables(#1, tables=['Artist'], clarify=False)",
        "generate_sql",
        "validate_execute(ok, rows=3)",
    ]
    assert state["trace"][-2:] == ["summarize", "finalize"]


async def check_repair_path() -> None:
    output = await _run(
        {
            "select_tables": FakeChain([_selection(["Artist"])]),
            "generate_sql": FakeChain([_draft(BAD_SQL)]),
            "rewrite_sql": FakeChain([_draft(GOOD_SQL)]),
            "summarize": FakeChain([_answer()]),
        }
    )
    print(f"改写后成功：repairs={output.repair_attempts} rows={output.row_count}")
    assert output.error is None
    assert output.repair_attempts == 1
    assert output.row_count == 3
    assert output.sql == GOOD_SQL


async def check_cannot_fix() -> None:
    output = await _run(
        {
            "select_tables": FakeChain([_selection(["Artist"])]),
            "generate_sql": FakeChain([_draft(BAD_SQL)]),
            "rewrite_sql": FakeChain(
                [_draft("", cannot_fix=True)]
            ),
        }
    )
    print(f"无法修复：answer={output.answer}")
    assert "无法回答该问题" in output.answer
    assert output.error


async def check_repair_exhausted() -> None:
    output = await _run(
        {
            "select_tables": FakeChain([_selection(["Artist"])]),
            "generate_sql": FakeChain([_draft(BAD_SQL)]),
            "rewrite_sql": FakeChain([_draft(BAD_SQL)]),
        },
        max_repair_attempts=1,
    )
    print(f"修复次数耗尽：repairs={output.repair_attempts} answer={output.answer}")
    assert output.repair_attempts == 1
    assert "已达到最大修复次数" in output.answer
    assert output.error


async def check_clarification() -> None:
    output = await _run(
        {
            "select_tables": FakeChain(
                [
                    TableSelection(
                        tables=[],
                        reason="信息不足",
                        needs_clarification=True,
                        clarification_question="你指的是哪位艺术家？",
                    )
                ]
            ),
            # 其余链不应被调用：空的 FakeChain 一旦被调用就会抛断言。
            "generate_sql": FakeChain([]),
        }
    )
    print(f"澄清路径：answer={output.answer}")
    assert output.answer == "你指的是哪位艺术家？"
    assert output.sql is None


async def check_invalid_selection_exhausted() -> None:
    selector = FakeChain([_selection(["Nope"]), _selection(["Nope"])])
    output = await _run(
        {
            "select_tables": selector,
            "generate_sql": FakeChain([]),
        }
    )
    print(f"选表耗尽：calls={selector.calls} answer={output.answer}")
    assert selector.calls == 2
    assert output.answer.startswith("无法确定有效的查询表")


async def main() -> None:
    print(f"数据库：{settings.db_path}")
    check_graph_shape()
    await check_happy_path()
    await check_repair_path()
    await check_cannot_fix()
    await check_repair_exhausted()
    await check_clarification()
    await check_invalid_selection_exhausted()
    print("图流程冒烟测试通过。")


if __name__ == "__main__":
    asyncio.run(main())
