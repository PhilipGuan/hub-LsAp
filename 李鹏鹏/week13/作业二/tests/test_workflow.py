import asyncio
from pathlib import Path

import pytest

import sql_agent.graph as graph_module
from sql_agent.config import settings
from sql_agent.db.catalog import SchemaCatalog
from sql_agent.db.executor import SQLExecutionError, SQLExecutor
from sql_agent.db.validator import SQLValidator
from sql_agent.models import ErrorCode, FinalAnswer, SQLDraft, TableSelection
from sql_agent.workflow import SQLAgentWorkflow


@pytest.fixture(scope="module")
def workflow() -> SQLAgentWorkflow:
    catalog = SchemaCatalog(Path("ref/chinook.db"))
    return SQLAgentWorkflow(
        catalog=catalog,
        validator=SQLValidator(catalog.db_path),
        executor=SQLExecutor(catalog.db_path),
    )


def _patch_agents(monkeypatch, outputs):
    """按顺序返回预设结果，模拟四个 LangChain 链的 LLM 调用。"""

    calls = []

    def make_fake(stage):
        async def fake(**kwargs):
            calls.append((stage, kwargs))
            return outputs[len(calls) - 1]

        return fake

    monkeypatch.setattr(graph_module, "select_tables", make_fake("table_select"))
    monkeypatch.setattr(graph_module, "generate_sql", make_fake("sql_generate"))
    monkeypatch.setattr(graph_module, "rewrite_sql", make_fake("sql_rewrite"))
    monkeypatch.setattr(graph_module, "summarize", make_fake("summarize"))
    return calls


def test_happy_path(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    calls = _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("总共有多少张专辑？"))

    assert result.error is None
    assert result.tables == ["albums"]
    assert result.row_count == 1
    assert result.columns == ["n"]
    assert result.rows == [["347"]]
    assert result.answer == "共 347 张专辑。"
    assert len(calls) == 3


def test_rewrite_path(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(ArtistId FROM albums", explanation="bad"),
        SQLDraft(sql="SELECT COUNT(ArtistId) AS n FROM albums", explanation="fixed"),
        FinalAnswer(answer="共有 275 位艺人。", evidence=["275"]),
    ]
    _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("专辑里有多少位不同的艺人？"))

    assert result.repair_attempts == 1
    assert result.error is None
    assert "COUNT(ArtistId)" in (result.sql or "")


def test_empty_result_skips_summary_agent(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="查不存在的专辑", confidence=0.9),
        SQLDraft(sql="SELECT * FROM albums WHERE AlbumId = -1", explanation="empty"),
    ]
    calls = _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("编号为 -1 的专辑是什么？"))

    assert result.row_count == 0
    assert result.answer == "未查询到符合条件的数据。"
    assert len(calls) == 2


def test_unknown_table_is_rejected(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["not_a_table"], reason="bad", confidence=0.1),
        TableSelection(tables=["albums"], reason="fixed", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("总共有多少张专辑？"))

    assert result.error is None
    assert result.tables == ["albums"]


def test_max_repair_attempts_returns_error(monkeypatch) -> None:
    catalog = SchemaCatalog(Path("ref/chinook.db"))
    workflow = SQLAgentWorkflow(
        catalog=catalog,
        validator=SQLValidator(catalog.db_path),
        executor=SQLExecutor(catalog.db_path),
        max_repair_attempts=0,
    )
    outputs = [
        TableSelection(tables=["albums"], reason="bad sql", confidence=0.9),
        SQLDraft(sql="SELECT nope FROM albums", explanation="bad"),
    ]
    _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("测试失败路径"))

    assert result.error is not None
    assert result.repair_attempts == 0
    assert "最大修复次数" in result.answer


def test_graph_contains_expected_nodes(workflow: SQLAgentWorkflow) -> None:
    nodes = set(workflow.graph.get_graph().nodes)
    assert {
        "select_tables",
        "check_selection",
        "generate_sql",
        "validate_sql",
        "execute_sql",
        "rewrite_sql",
        "summarize",
        "empty_result",
        "exhausted",
    } <= nodes


def test_clarification_stops_workflow(
    workflow: SQLAgentWorkflow, monkeypatch
) -> None:
    outputs = [
        TableSelection(
            tables=[],
            reason="问题不明确",
            confidence=0.1,
            needs_clarification=True,
            clarification_question="你想查询哪张表？",
        )
    ]
    calls = _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("这个有多少？"))

    assert result.answer == "你想查询哪张表？"
    assert result.error is None
    assert len(calls) == 1


def test_cannot_fix_stops_repair_loop(
    workflow: SQLAgentWorkflow, monkeypatch
) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="统计", confidence=0.9),
        SQLDraft(sql="SELECT nope FROM albums", explanation="bad"),
        SQLDraft(
            sql="SELECT nope FROM albums",
            explanation="列 nope 不存在",
            cannot_fix=True,
        ),
    ]
    calls = _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("查询不存在的列"))

    assert result.repair_attempts == 0
    assert "无法回答" in result.answer
    assert result.error is not None
    assert len(calls) == 3


def test_execution_error_triggers_rewrite(monkeypatch) -> None:
    class FailOnceExecutor(SQLExecutor):
        def __init__(self, db_path: Path) -> None:
            super().__init__(db_path)
            self.calls = 0

        def execute(self, sql: str):
            self.calls += 1
            if self.calls == 1:
                raise SQLExecutionError("boom", ErrorCode.RUNTIME)
            return super().execute(sql)

    catalog = SchemaCatalog(Path("ref/chinook.db"))
    workflow = SQLAgentWorkflow(
        catalog=catalog,
        validator=SQLValidator(catalog.db_path),
        executor=FailOnceExecutor(catalog.db_path),
    )
    outputs = [
        TableSelection(tables=["albums"], reason="统计", confidence=0.9),
        SQLDraft(sql="SELECT 1", explanation="runtime"),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="fixed"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("总共有多少张专辑？"))

    assert result.repair_attempts == 1
    assert result.error is None
    assert result.answer == "共 347 张专辑。"


def test_settings_db_path_exists() -> None:
    assert settings.db_path.exists()
