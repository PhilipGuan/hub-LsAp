import asyncio
from collections.abc import AsyncIterator
import json
from threading import Event
from time import monotonic

from agent import sql_agent, summary_agent, table_agent
from models.chat import AnswerDelta, ChatEvent, ChatRequest, ChatResponse, ErrorEvent, ResultEvent
from models.workflow import WorkflowFailure, WorkflowStep
from services.database_service import DatabaseService
from services.llm_service import LLMService


async def stream_ask(request: ChatRequest, cancel: Event | None = None,
                     llm_factory=LLMService, database_factory=DatabaseService) -> AsyncIterator[ChatEvent]:
    cancel = cancel or Event()
    response = ChatResponse()
    stage = "schema"
    llm = None
    started = monotonic()

    def step(name, status, message):
        nonlocal stage, started
        stage = name
        elapsed = 0 if status == "running" else round((monotonic() - started) * 1000, 2)
        if status == "running":
            started = monotonic()
        record = WorkflowStep(stage=name, status=status, message=message,
                              attempt=response.rewrite_count, elapsed_ms=elapsed)
        response.steps.append(record)
        return ChatEvent(event="step", data=record)

    try:
        yield step("schema", "running", "正在解析数据库结构")
        database = database_factory(cancel=cancel)
        schema = await asyncio.to_thread(database.parse_schema)
        yield step("schema", "completed", f"已解析 {len(schema.tables)} 张业务表")
        yield step("table_selection", "running", "正在根据问题选择数据表")
        llm = llm_factory()
        selection = await table_agent.select_tables(llm, request.question, schema)
        if selection.clarification:
            response.status = "needs_clarification"
            response.answer = selection.clarification
            yield step("table_selection", "completed", "需要补充查询口径")
            yield ChatEvent(event="done", data=response)
            return
        selection = database.complete_selection(selection, schema)
        response.selected_tables = selection.tables
        yield step("table_selection", "completed", "已选择：" + "、".join(selection.tables))
        yield step("sql_generation", "running", "正在生成 SQL")
        candidate = await sql_agent.generate_sql(llm, request.question, schema, selection)
        yield step("sql_generation", "completed", "SQL 已生成")
        seen_errors = set()
        while True:
            if cancel.is_set():
                raise asyncio.CancelledError
            response.sql, response.params = candidate.sql, candidate.params
            yield step("sql_check", "running", "正在校验并预执行 SQL")
            checked = await asyncio.to_thread(database.check_sql, candidate, schema, selection.tables)
            error = checked.error
            if checked.passed:
                yield step("sql_check", "completed", "SQL 校验与预执行通过")
                yield step("sql_execution", "running", "正在正式执行 SQL")
                try:
                    result = await asyncio.to_thread(database.execute_sql, candidate, selection.tables)
                    yield step("sql_execution", "completed", f"查询成功，返回 {result.returned_rows} 行")
                    break
                except WorkflowFailure as exc:
                    error = exc.error
            yield step(stage, "failed", error.message)
            if not error.retryable:
                raise WorkflowFailure(**error.model_dump())
            fingerprint = (candidate.sql, json.dumps(candidate.params, sort_keys=True), error.code, error.message)
            if fingerprint in seen_errors:
                raise WorkflowFailure(error.stage, "retry_exhausted", "相同 SQL 和错误重复出现，已停止改写。")
            seen_errors.add(fingerprint)
            if response.rewrite_count >= 3:
                raise WorkflowFailure(error.stage, "retry_exhausted", "已达到 SQL 改写上限。最后错误：" + error.message)
            response.rewrite_count += 1
            if error.code == "selection_error":
                yield step("table_selection", "running", "正在根据错误补充选表")
                selection = await table_agent.select_tables(llm, request.question, schema, error.message)
                if selection.clarification:
                    response.status, response.answer = "needs_clarification", selection.clarification
                    yield step("table_selection", "completed", "需要补充查询口径")
                    yield ChatEvent(event="done", data=response)
                    return
                selection = database.complete_selection(selection, schema)
                response.selected_tables = selection.tables
                yield step("table_selection", "completed", "已更新选表：" + "、".join(selection.tables))
            yield step("sql_rewrite", "running", f"正在进行第 {response.rewrite_count} 次 SQL 改写")
            candidate = await sql_agent.rewrite_sql(llm, request.question, candidate, error, schema, selection)
            yield step("sql_rewrite", "completed", "已改写 SQL，将重新校验")

        for field in ("columns", "rows", "returned_rows", "truncated"):
            setattr(response, field, getattr(result, field))
        yield ChatEvent(event="result", data=ResultEvent(**result.model_dump(),
                        selected_tables=selection.tables, sql=candidate.sql, params=candidate.params))
        yield step("summary", "running", "正在汇总查询结果")
        try:
            async for delta in summary_agent.stream_summary(llm, request.question, candidate, result):
                response.answer += delta
                yield ChatEvent(event="answer_delta", data=AnswerDelta(delta=delta))
            if not response.answer.strip():
                raise WorkflowFailure("summary", "summary_error", "汇总没有返回文本。")
            yield step("summary", "completed", "回答已完成")
        except WorkflowFailure as exc:
            response.summary_degraded = True
            response.error = exc.error
            response.answer = (f"查询成功，返回 {result.returned_rows} 行。" if result.returned_rows
                               else "查询成功，没有匹配记录。")
            if result.truncated:
                response.answer += "结果已截断，仅展示部分记录。"
            response.answer += "自动汇总暂不可用，请查看查询结果和 SQL。"
            yield step("summary", "failed", "汇总失败，已保留查询结果")
            yield ChatEvent(event="error", data=ErrorEvent(stage="summary", code=exc.error.code,
                            message=exc.error.message, fatal=False))
        response.status = "success"
        yield ChatEvent(event="done", data=response)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = (exc.error if isinstance(exc, WorkflowFailure) else
                 WorkflowFailure(stage, "internal_error", "处理失败，请检查后端配置或重新提交。" ).error)
        if error.stage == "model":
            error = error.model_copy(update={"stage": stage})
        response.status, response.error = "failed", error
        response.answer = error.message
        if not response.steps or response.steps[-1].status != "failed":
            yield step(error.stage, "failed", error.message)
        yield ChatEvent(event="error", data=ErrorEvent(stage=error.stage, code=error.code,
                        message=error.message, fatal=True))
        yield ChatEvent(event="done", data=response)
    finally:
        cancel.set()
        if llm is not None:
            await llm.close()
