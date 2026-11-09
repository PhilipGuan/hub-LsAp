"""整图测试：fake LLM + fake catalog + 真实 validator/executor（chinook.db）。

覆盖设计文档 §9.2 的场景：
正常路径、校验失败修复回边、预算耗尽、cannot_fix、选表反馈重试、
澄清、空结果、汇总降级、流式与回放。
"""

from sql_agent_langgraph.config import settings
from sql_agent_langgraph.db.executor import SQLExecutor
from sql_agent_langgraph.db.validator import SQLValidator
from sql_agent_langgraph.graph import SQLAgentGraph, build_graph
from sql_agent_langgraph.llm import LLMBundle
from sql_agent_langgraph.models import FinalAnswer, SQLDraft, TableSelection

from conftest import FakeCatalog, FakeStructuredLLM

CATALOG = FakeCatalog(["albums", "artists", "tracks"])
DB_PATH = settings.db_path


def _bundle(
    selection: list,
    generation: list,
    rewrite: list | None = None,
    summary: list | None = None,
) -> tuple[LLMBundle, dict[str, FakeStructuredLLM]]:
    """构造 LLMBundle，同时返回各 fake 以便断言调用次数。"""

    selection_llm = FakeStructuredLLM(selection)
    generation_llm = FakeStructuredLLM(generation)
    rewrite_llm = FakeStructuredLLM(rewrite or [])
    summary_llm = FakeStructuredLLM(summary or [])
    bundle = LLMBundle(
        selection=selection_llm,
        generation=generation_llm,
        rewrite=rewrite_llm,
        summary=summary_llm,
    )
    fakes = {
        "selection": selection_llm,
        "generation": generation_llm,
        "rewrite": rewrite_llm,
        "summary": summary_llm,
    }
    return bundle, fakes


def _workflow(bundle: LLMBundle) -> SQLAgentGraph:
    return SQLAgentGraph(
        catalog=CATALOG,
        validator=SQLValidator(DB_PATH),
        executor=SQLExecutor(DB_PATH),
        llms=bundle,
    )


GOOD_SELECTION = TableSelection(tables=["albums"], reason="查专辑数", confidence=0.9)
GOOD_DRAFT = SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="统计专辑总数")
GOOD_ANSWER = FinalAnswer(answer="共 347 张专辑。", evidence=["347"])


def test_happy_path() -> None:
    bundle, fakes = _bundle([GOOD_SELECTION], [GOOD_DRAFT], [], [GOOD_ANSWER])
    workflow = _workflow(bundle)
    output = workflow.run("数据库里有多少张专辑？")

    assert output.error is None
    assert output.tables == ["albums"]
    assert output.row_count == 1
    assert output.answer == "共 347 张专辑。"
    assert output.repair_attempts == 0
    assert fakes["selection"].call_count == 1
    assert fakes["generation"].call_count == 1
    assert fakes["rewrite"].call_count == 0
    assert fakes["summary"].call_count == 1
    # 用户问题必须包在 <question> 标签中作为数据传入（防注入约定）。
    assert "<question>" in fakes["selection"].calls[0]


def test_repair_loop_recovers_from_bad_sql() -> None:
    bad_draft = SQLDraft(sql="SELECT * FROM nope", explanation="写错了表")
    bundle, fakes = _bundle(
        [GOOD_SELECTION], [bad_draft], [GOOD_DRAFT], [GOOD_ANSWER]
    )
    workflow = _workflow(bundle)
    output = workflow.run("数据库里有多少张专辑？")

    assert output.error is None
    assert output.sql == "SELECT COUNT(*) AS n FROM albums"
    assert output.repair_attempts == 1
    assert fakes["rewrite"].call_count == 1
    # 修复 prompt 必须带错误码与已尝试过的 SQL。
    rewrite_prompt = fakes["rewrite"].calls[0]
    assert "unknown_object" in rewrite_prompt
    assert "SELECT * FROM nope" in rewrite_prompt


def test_repair_budget_exhausted() -> None:
    bad_draft = SQLDraft(sql="SELECT * FROM nope")
    bundle, fakes = _bundle([GOOD_SELECTION], [bad_draft], [bad_draft] * 3, [])
    workflow = _workflow(bundle)
    output = workflow.run("数据库里有多少张专辑？")

    assert output.answer == "无法生成可执行的 SQL，已达到最大修复次数。"
    assert output.repair_attempts == 3
    assert fakes["rewrite"].call_count == 3
    assert output.error


def test_rewrite_cannot_fix_stops_without_budget() -> None:
    bad_draft = SQLDraft(sql="SELECT * FROM nope")
    cannot = SQLDraft(sql="", cannot_fix=True, explanation="问题超出库内容")
    bundle, fakes = _bundle([GOOD_SELECTION], [bad_draft], [cannot], [])
    workflow = _workflow(bundle)
    output = workflow.run("数据库里有多少张专辑？")

    assert output.answer.startswith("当前数据库无法回答该问题：")
    assert output.repair_attempts == 0  # cannot_fix 不计预算
    assert fakes["rewrite"].call_count == 1
    assert fakes["summary"].call_count == 0


def test_selection_feedback_retry_then_success() -> None:
    bad_selection = TableSelection(tables=["nope"], reason="臆造表名")
    bundle, fakes = _bundle(
        [bad_selection, GOOD_SELECTION], [GOOD_DRAFT], [], [GOOD_ANSWER]
    )
    workflow = _workflow(bundle)
    output = workflow.run("数据库里有多少张专辑？")

    assert output.error is None
    assert fakes["selection"].call_count == 2
    # 第二次选表的 prompt 必须带上一轮的问题反馈。
    assert "上一次选择的问题" in fakes["selection"].calls[1]
    assert "表不存在" in fakes["selection"].calls[1]


def test_selection_invalid_after_retry_finalizes() -> None:
    bad_selection = TableSelection(tables=["nope"], reason="臆造表名")
    bundle, fakes = _bundle([bad_selection, bad_selection], [], [], [])
    workflow = _workflow(bundle)
    output = workflow.run("数据库里有多少张专辑？")

    assert output.answer.startswith("无法确定有效的查询表：")
    assert fakes["selection"].call_count == 2
    assert fakes["generation"].call_count == 0


def test_clarification_short_circuits() -> None:
    clarify = TableSelection(
        tables=[],
        reason="问题与库内容无关",
        needs_clarification=True,
        clarification_question="请问指哪一年？",
    )
    bundle, fakes = _bundle([clarify], [], [], [])
    workflow = _workflow(bundle)
    output = workflow.run("销量如何？")

    assert output.answer == "请问指哪一年？"
    assert fakes["generation"].call_count == 0
    assert fakes["summary"].call_count == 0


def test_empty_result_skips_summary_llm() -> None:
    empty_draft = SQLDraft(sql="SELECT * FROM albums WHERE AlbumId = -1")
    bundle, fakes = _bundle([GOOD_SELECTION], [empty_draft], [], [])
    workflow = _workflow(bundle)
    output = workflow.run("有哪些不存在的专辑？")

    assert output.answer == "未查询到符合条件的数据。"
    assert output.row_count == 0
    assert fakes["summary"].call_count == 0


def test_summary_fallback_to_markdown_table() -> None:
    class Boom(Exception):
        pass

    bundle, fakes = _bundle(
        [GOOD_SELECTION],
        [GOOD_DRAFT],
        [],
        [Boom("LLM 不可用")],
    )
    workflow = _workflow(bundle)
    output = workflow.run("数据库里有多少张专辑？")

    assert "查询返回 1 行。" in output.answer
    assert "| n |" in output.answer
    assert any("降级" in caveat for caveat in output.caveats)
    assert output.error is None  # 降级不是失败


def test_empty_question_guard() -> None:
    bundle, fakes = _bundle([], [], [], [])
    workflow = _workflow(bundle)
    output = workflow.run("   ")
    assert output.answer == "请输入要查询的问题。"
    assert all(fake.call_count == 0 for fake in fakes.values())


def test_streaming_and_checkpoint_replay() -> None:
    bundle, _ = _bundle([GOOD_SELECTION], [GOOD_DRAFT], [], [GOOD_ANSWER])
    workflow = _workflow(bundle)

    updates: list[str] = []
    output = workflow.run_streaming(
        "数据库里有多少张专辑？", thread_id="t-replay", on_update=lambda n, u: updates.append(n)
    )
    assert output is not None and output.error is None
    assert updates[0] == "select_tables"
    assert "finalize" in updates

    # 回放（教程 04）：状态历史可按 checkpoint 遍历。
    history = workflow.state_history("t-replay")
    assert len(history) >= 5
    nexts = {state.next for state in history}
    assert any("select_tables" in n for n in nexts)


def test_build_graph_mermaid_topology() -> None:
    bundle, _ = _bundle([], [], [], [])
    graph = build_graph(
        CATALOG,
        SQLValidator(DB_PATH),
        SQLExecutor(DB_PATH),
        bundle,
    )
    mermaid = graph.get_graph().draw_mermaid()
    for node in (
        "select_tables",
        "generate_sql",
        "validate_sql",
        "execute_sql",
        "rewrite_sql",
        "summarize",
        "finalize",
    ):
        assert node in mermaid
