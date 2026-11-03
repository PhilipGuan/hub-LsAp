"""路由函数单测：无 LLM、无真实数据库，逐分支覆盖。"""

from sql_agent_langgraph.models import ExecutionResult, SQLDraft, TableSelection, ValidationResult
from sql_agent_langgraph.routing import (
    make_route_execute,
    make_route_selection,
    make_route_validate,
    route_after_rewrite,
    selection_problems,
)

from conftest import FakeCatalog

CATALOG = FakeCatalog(
    ["albums", "artists", "tracks"],
    connected={frozenset({"albums", "artists"}), frozenset({"albums", "artists", "tracks"})},
)
MAX_REPAIR = 3


def _state(**kwargs) -> dict:
    base = {"selection_retries": 0, "repair_attempts": 0}
    base.update(kwargs)
    return base


# -- selection_problems ------------------------------------------------------


def test_selection_problems_ok() -> None:
    assert selection_problems(["albums"], CATALOG) == []
    assert selection_problems(["albums", "artists"], CATALOG) == []


def test_selection_problems_empty() -> None:
    assert selection_problems([], CATALOG) == ["未选择任何表"]


def test_selection_problems_unknown_table() -> None:
    problems = selection_problems(["albums", "nope"], CATALOG)
    assert len(problems) == 1
    assert "nope" in problems[0]
    assert "可选表" in problems[0]


def test_selection_problems_disconnected() -> None:
    assert "外键连接" in selection_problems(["albums", "tracks"], CATALOG)[0]


# -- route_selection ---------------------------------------------------------


def test_route_selection_ok() -> None:
    route = make_route_selection(CATALOG)
    state = _state(selection=TableSelection(tables=["albums"], reason="单表查询"))
    assert route(state) == "generate_sql"


def test_route_selection_clarification_goes_finalize() -> None:
    route = make_route_selection(CATALOG)
    state = _state(
        selection=TableSelection(
        tables=[], reason="无法判断", needs_clarification=True, clarification_question="?"
    )
    )
    assert route(state) == "finalize"


def test_route_selection_invalid_retries_then_finalize() -> None:
    route = make_route_selection(CATALOG)
    bad = _state(selection=TableSelection(tables=["albums", "tracks"], reason="缺少连接"))
    assert route(bad) == "select_tables"  # 第一次非法：反馈重试

    exhausted = _state(selection=TableSelection(tables=["albums", "tracks"], reason="缺少连接"), selection_retries=1)
    assert route(exhausted) == "finalize"  # 重试后仍非法：终止


# -- route_validate ----------------------------------------------------------


def test_route_validate_ok_goes_execute() -> None:
    route = make_route_validate(MAX_REPAIR)
    state = _state(validation=ValidationResult(ok=True, normalized_sql="SELECT 1"))
    assert route(state) == "execute_sql"


def test_route_validate_failed_rewrites_within_budget() -> None:
    route = make_route_validate(MAX_REPAIR)
    failed = ValidationResult(ok=False, message="boom")
    assert route(_state(validation=failed)) == "rewrite_sql"
    assert route(_state(validation=failed, repair_attempts=2)) == "rewrite_sql"


def test_route_validate_budget_exhausted_goes_finalize() -> None:
    route = make_route_validate(MAX_REPAIR)
    failed = ValidationResult(ok=False, message="boom")
    assert route(_state(validation=failed, repair_attempts=3)) == "finalize"


# -- route_execute -----------------------------------------------------------


def test_route_execute_success_goes_summarize() -> None:
    route = make_route_execute(MAX_REPAIR)
    state = _state(execution=ExecutionResult(columns=["n"], rows=[["1"]], row_count=1))
    assert route(state) == "summarize"


def test_route_execute_error_shares_repair_budget() -> None:
    route = make_route_execute(MAX_REPAIR)
    assert route(_state()) == "rewrite_sql"
    assert route(_state(repair_attempts=3)) == "finalize"


# -- route_after_rewrite -----------------------------------------------------


def test_route_after_rewrite_valid() -> None:
    state = _state(draft=SQLDraft(sql="SELECT 1"))
    assert route_after_rewrite(state) == "validate_sql"


def test_route_after_rewrite_cannot_fix() -> None:
    state = _state(draft=SQLDraft(sql="", cannot_fix=True, explanation="无法回答"))
    assert route_after_rewrite(state) == "finalize"
