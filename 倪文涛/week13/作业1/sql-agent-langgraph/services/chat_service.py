"""用 StateGraph 编排 SQL 问答，直接输出 SSE 业务事件。"""
import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
import json
from threading import Event
from time import monotonic
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from agent import sql_agent, summary_agent, table_agent
from models.chat import AnswerDelta, ChatEvent, ChatRequest, ChatResponse, ErrorEvent, ResultEvent
from models.workflow import (
    DatabaseSchema, QueryResult, SQLCandidate, TableSelection, WorkflowError,
    WorkflowFailure, WorkflowStep,
)
from services.database_service import DatabaseService


class WorkflowState(TypedDict, total=False):
    question: str
    schema: DatabaseSchema
    selection: TableSelection
    candidate: SQLCandidate
    result: QueryResult
    error: WorkflowError | None
    seen_errors: set[tuple[str, str, str, str]]
    rewrite_count: int
    needs_clarification: bool


async def stream_ask(request: ChatRequest, cancel: Event | None = None,
                     database_factory=DatabaseService) -> AsyncIterator[ChatEvent]:
    cancel = cancel or Event()
    response = ChatResponse()
    stage = "schema"
    started = monotonic()
    database = None

    def emit(event, data):
        get_stream_writer()(ChatEvent(event=event, data=data))

    def step(name, status, message):
        nonlocal stage, started
        stage = name
        elapsed = 0 if status == "running" else round((monotonic() - started) * 1000, 2)
        if status == "running":
            if cancel.is_set():
                raise asyncio.CancelledError
            started = monotonic()
        record = WorkflowStep(stage=name, status=status, message=message,
                              attempt=response.rewrite_count, elapsed_ms=elapsed)
        response.steps.append(record)
        emit("step", record)

    async def schema(state: WorkflowState):
        nonlocal database
        step("schema", "running", "正在解析数据库结构")
        database = database_factory(cancel=cancel)
        schema = await asyncio.to_thread(database.parse_schema)
        step("schema", "completed", f"已解析 {len(schema.tables)} 张业务表")
        return {"schema": schema, "rewrite_count": 0, "seen_errors": set(), "error": None}

    async def table_selection(state: WorkflowState):
        step("table_selection", "running", "正在根据问题选择数据表")
        error = state.get("error")
        selection = await table_agent.select_tables(
            state["question"], state["schema"], error.message if error else None)
        if selection.clarification:
            response.status = "needs_clarification"
            response.answer = selection.clarification
            step("table_selection", "completed", "需要补充查询口径")
            return {"selection": selection, "needs_clarification": True}
        selection = database.complete_selection(selection, state["schema"])
        response.selected_tables = selection.tables
        step("table_selection", "completed", "已选择：" + "、".join(selection.tables))
        return {"selection": selection, "needs_clarification": False}

    def after_selection(state: WorkflowState):
        if state.get("needs_clarification"):
            return END
        return "sql_rewrite" if state["rewrite_count"] else "sql_generation"

    async def sql_generation(state: WorkflowState):
        step("sql_generation", "running", "正在生成 SQL")
        candidate = await sql_agent.generate_sql(
            state["question"], state["schema"], state["selection"])
        step("sql_generation", "completed", "SQL 已生成")
        return {"candidate": candidate}

    async def sql_check(state: WorkflowState):
        candidate = state["candidate"]
        response.sql, response.params = candidate.sql, candidate.params
        step("sql_check", "running", "正在校验并预执行 SQL")
        checked = await asyncio.to_thread(
            database.check_sql, candidate, state["schema"], state["selection"].tables)
        if not checked.passed:
            if checked.error is None:
                raise WorkflowFailure("sql_check", "internal_error", "SQL 校验失败但未返回错误。")
            step("sql_check", "failed", checked.error.message)
        else:
            step("sql_check", "completed", "SQL 校验与预执行通过")
        return {"error": checked.error if not checked.passed else None}

    def after_check(state: WorkflowState):
        return "retry" if state["error"] else "sql_execution"

    async def sql_execution(state: WorkflowState):
        step("sql_execution", "running", "正在正式执行 SQL")
        try:
            result = await asyncio.to_thread(
                database.execute_sql, state["candidate"], state["selection"].tables)
        except WorkflowFailure as exc:
            step("sql_execution", "failed", exc.error.message)
            return {"error": exc.error}
        step("sql_execution", "completed", f"查询成功，返回 {result.returned_rows} 行")
        for field in ("columns", "rows", "returned_rows", "truncated"):
            setattr(response, field, getattr(result, field))
        emit("result", ResultEvent(**result.model_dump(), selected_tables=state["selection"].tables,
                                       sql=state["candidate"].sql, params=state["candidate"].params))
        return {"result": result, "error": None}

    def after_execution(state: WorkflowState):
        return "retry" if state["error"] else "summary"

    async def retry(state: WorkflowState):
        error, candidate = state["error"], state["candidate"]
        if not error.retryable:
            raise WorkflowFailure(**error.model_dump())
        fingerprint = (candidate.sql, json.dumps(candidate.params, sort_keys=True), error.code, error.message)
        if fingerprint in state["seen_errors"]:
            raise WorkflowFailure(error.stage, "retry_exhausted", "相同 SQL 和错误重复出现，已停止改写。")
        if state["rewrite_count"] >= 3:
            raise WorkflowFailure(error.stage, "retry_exhausted", "已达到 SQL 改写上限。最后错误：" + error.message)
        count = state["rewrite_count"] + 1
        response.rewrite_count = count
        return {"rewrite_count": count, "seen_errors": state["seen_errors"] | {fingerprint}}

    def after_retry(state: WorkflowState):
        return "table_selection" if state["error"].code == "selection_error" else "sql_rewrite"

    async def sql_rewrite(state: WorkflowState):
        step("sql_rewrite", "running", f"正在进行第 {state['rewrite_count']} 次 SQL 改写")
        candidate = await sql_agent.rewrite_sql(
            state["question"], state["candidate"], state["error"],
            state["schema"], state["selection"])
        step("sql_rewrite", "completed", "已改写 SQL，将重新校验")
        return {"candidate": candidate}

    async def summary(state: WorkflowState):
        result = state["result"]
        step("summary", "running", "正在汇总查询结果")
        try:
            async with aclosing(summary_agent.stream_summary(
                    state["question"], state["candidate"], result)) as deltas:
                async for delta in deltas:
                    response.answer += delta
                    emit("answer_delta", AnswerDelta(delta=delta))
            if not response.answer.strip():
                raise WorkflowFailure("summary", "summary_error", "汇总没有返回文本。")
            step("summary", "completed", "回答已完成")
        except WorkflowFailure as exc:
            response.summary_degraded = True
            response.error = exc.error
            response.answer = (f"查询成功，返回 {result.returned_rows} 行。" if result.returned_rows
                                    else "查询成功，没有匹配记录。")
            if result.truncated:
                response.answer += "结果已截断，仅展示部分记录。"
            response.answer += "自动汇总暂不可用，请查看查询结果和 SQL。"
            step("summary", "failed", "汇总失败，已保留查询结果")
            emit("error", ErrorEvent(**exc.error.model_dump(exclude={"retryable"}), fatal=False))
        response.status = "success"
        return {}

    # 和教程一致：声明节点 → 连接固定边/条件边 → 编译 → 运行。
    builder = StateGraph(WorkflowState)
    builder.add_node("schema", schema)
    builder.add_node("table_selection", table_selection)
    builder.add_node("sql_generation", sql_generation)
    builder.add_node("sql_check", sql_check)
    builder.add_node("sql_execution", sql_execution)
    builder.add_node("retry", retry)
    builder.add_node("sql_rewrite", sql_rewrite)
    builder.add_node("summary", summary)
    builder.add_edge(START, "schema")
    builder.add_edge("schema", "table_selection")
    builder.add_conditional_edges("table_selection", after_selection,
                                  [END, "sql_generation", "sql_rewrite"])
    builder.add_edge("sql_generation", "sql_check")
    builder.add_conditional_edges("sql_check", after_check, ["retry", "sql_execution"])
    builder.add_conditional_edges("sql_execution", after_execution, ["retry", "summary"])
    builder.add_conditional_edges("retry", after_retry, ["table_selection", "sql_rewrite"])
    builder.add_edge("sql_rewrite", "sql_check")
    builder.add_edge("summary", END)
    graph = builder.compile(name="SQL workflow")

    try:
        if cancel.is_set():
            raise asyncio.CancelledError
        # 最坏路径包含 3 次补选表、改写及重新校验，显式给出充足的图步数预算。
        async with aclosing(graph.astream(
                {"question": request.question}, config={"recursion_limit": 40},
                stream_mode="custom")) as events:
            async for event in events:
                yield event
        if cancel.is_set():
            raise asyncio.CancelledError
        yield ChatEvent(event="done", data=response)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = (exc.error if isinstance(exc, WorkflowFailure) else
                 WorkflowFailure(stage, "internal_error", "处理失败，请检查后端配置或重新提交。").error)
        if error.stage == "model":
            error = error.model_copy(update={"stage": stage})
        response.status, response.error = "failed", error
        response.answer = error.message
        if not response.steps or response.steps[-1].status != "failed":
            record = WorkflowStep(stage=error.stage, status="failed", message=error.message,
                                  attempt=response.rewrite_count,
                                  elapsed_ms=round((monotonic() - started) * 1000, 2))
            response.steps.append(record)
            yield ChatEvent(event="step", data=record)
        yield ChatEvent(event="error", data=ErrorEvent(stage=error.stage, code=error.code,
                        message=error.message, fatal=True))
        yield ChatEvent(event="done", data=response)
    finally:
        cancel.set()
