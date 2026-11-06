from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Literal, TypedDict
import json
import re

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph, add_messages

from .config import get_settings
from .database import DatabaseCatalog, UnsafeQueryError
from .models import SQLDraft, TableSelection
from .prompts import (
    ANSWER_SYSTEM,
    SQL_GENERATION_SYSTEM,
    SQL_REPAIR_SYSTEM,
    TABLE_SELECTION_SYSTEM,
)

settings = get_settings()
catalog = DatabaseCatalog(
    settings.database_url,
    row_limit=settings.row_limit,
)


class SQLAgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    selected_tables: list[str]
    selection_reason: str
    schema: str
    sql: str
    sql_explanation: str
    sql_error: str
    columns: list[str]
    rows: list[dict[str, Any]]
    answer: str
    retry_count: int


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    if not settings.llm_api_key:
        raise RuntimeError(
            "未配置模型密钥。请复制 .env.example 为 .env，并设置 LLM_API_KEY。"
        )

    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "api_key": settings.llm_api_key,
        "temperature": 0,
        "timeout": settings.request_timeout,
        "max_retries": 2,
    }
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    return ChatOpenAI(**kwargs)


def _message_text(message: AnyMessage) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text_value = block.get("text") or block.get("content")
                if text_value:
                    parts.append(str(text_value))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def _history_text(state: SQLAgentState, limit: int = 8) -> str:
    messages = list(state.get("messages", []))
    if messages and isinstance(messages[-1], HumanMessage):
        messages = messages[:-1]

    selected = messages[-limit:]
    if not selected:
        return "（无历史对话）"

    lines: list[str] = []
    for message in selected:
        role = "用户" if isinstance(message, HumanMessage) else "助手"
        text = _message_text(message).strip()
        if text:
            lines.append(f"{role}: {text[:1000]}")
    return "\n".join(lines) or "（无历史对话）"


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.IGNORECASE | re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型响应中没有找到 JSON 对象")
    return json.loads(candidate[start : end + 1])


def _structured_invoke(schema: type, messages: list[AnyMessage]) -> Any:
    llm = get_llm()
    try:
        return llm.with_structured_output(schema).invoke(messages)
    except Exception as structured_error:
        response = llm.invoke(
            [
                *messages,
                HumanMessage(
                    content=(
                        "请严格只返回一个合法 JSON 对象，不要 Markdown 代码块，"
                        "不要任何额外解释。"
                    )
                ),
            ]
        )
        try:
            payload = _extract_json_object(_message_text(response))
            return schema.model_validate(payload)
        except Exception as parse_error:
            raise RuntimeError(
                "模型没有返回可解析的结构化结果，请检查模型是否支持工具调用或 JSON 输出。"
            ) from structured_error


def _clean_sql(text: str) -> str:
    sql = text.strip()
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", sql, re.IGNORECASE | re.DOTALL)
    if fenced:
        sql = fenced.group(1).strip()

    sql = re.sub(r"^(sql|SQL)\s*[:：]\s*", "", sql).strip()
    return sql


def prepare_node(state: SQLAgentState) -> dict[str, Any]:
    question = (state.get("question") or "").strip()
    if not question:
        for message in reversed(state.get("messages", [])):
            if isinstance(message, HumanMessage):
                question = _message_text(message).strip()
                break

    if not question:
        raise ValueError("用户问题不能为空")

    return {
        "question": question,
        "selected_tables": [],
        "selection_reason": "",
        "schema": catalog.get_schema(),
        "sql": "",
        "sql_explanation": "",
        "sql_error": "",
        "columns": [],
        "rows": [],
        "answer": "",
        "retry_count": 0,
    }


def select_tables_node(state: SQLAgentState) -> dict[str, Any]:
    table_names = "\n".join(f"- {name}" for name in catalog.table_names)
    selection = _structured_invoke(
        TableSelection,
        [
            SystemMessage(content=TABLE_SELECTION_SYSTEM),
            HumanMessage(
                content=(
                    f"可用表：\n{table_names}\n\n"
                    f"数据库结构：\n{catalog.get_schema()}\n\n"
                    f"最近对话：\n{_history_text(state)}\n\n"
                    f"当前用户问题：{state['question']}"
                )
            ),
        ],
    )

    lookup = {name.lower(): name for name in catalog.table_names}
    selected: list[str] = []
    for table in selection.tables:
        actual_name = lookup.get(str(table).strip().lower())
        if actual_name and actual_name not in selected:
            selected.append(actual_name)

    # 如果模型选表失败，退回完整 schema，保证后续节点仍可工作。
    if not selected:
        selected = list(catalog.table_names)

    return {
        "selected_tables": selected,
        "selection_reason": selection.reason,
        "schema": catalog.get_schema(selected),
    }


def generate_sql_node(state: SQLAgentState) -> dict[str, Any]:
    draft = _structured_invoke(
        SQLDraft,
        [
            SystemMessage(content=SQL_GENERATION_SYSTEM),
            HumanMessage(
                content=(
                    f"用户问题：{state['question']}\n\n"
                    f"最近对话：\n{_history_text(state)}\n\n"
                    f"相关表结构：\n{state['schema']}\n\n"
                    "请生成一条只读 SQL。"
                )
            ),
        ],
    )
    sql = _clean_sql(draft.sql)
    if not sql:
        raise ValueError("模型生成了空 SQL")
    return {"sql": sql, "sql_explanation": draft.explanation, "sql_error": ""}


def execute_sql_node(state: SQLAgentState) -> dict[str, Any]:
    try:
        result = catalog.execute(state["sql"])
        return {
            "columns": result.columns,
            "rows": result.rows,
            "sql_error": "",
        }
    except Exception as exc:
        error = str(exc)
        if isinstance(exc, UnsafeQueryError):
            error = f"安全校验失败：{error}"
        return {
            "sql_error": error[:2000],
            "columns": [],
            "rows": [],
            "retry_count": state.get("retry_count", 0) + 1,
        }


def repair_sql_node(state: SQLAgentState) -> dict[str, Any]:
    draft = _structured_invoke(
        SQLDraft,
        [
            SystemMessage(content=SQL_REPAIR_SYSTEM),
            HumanMessage(
                content=(
                    f"用户问题：{state['question']}\n\n"
                    f"相关表结构：\n{state['schema']}\n\n"
                    f"执行失败的 SQL：\n{state['sql']}\n\n"
                    f"数据库报错：\n{state['sql_error']}\n\n"
                    "请修复并只返回一条 SELECT 或 WITH SQL。"
                )
            ),
        ],
    )
    sql = _clean_sql(draft.sql)
    if not sql:
        raise ValueError("SQL 修复节点生成了空 SQL")
    return {
        "sql": sql,
        "sql_explanation": draft.explanation,
        "sql_error": "",
    }


def summarize_answer_node(state: SQLAgentState) -> dict[str, Any]:
    if state.get("sql_error"):
        answer = (
            f"SQL 在执行 {state.get('retry_count', 0)} 次后仍然失败。\n\n"
            f"最后一次错误：{state['sql_error']}"
        )
    else:
        rows_text = json.dumps(
            state.get("rows", []),
            ensure_ascii=False,
            default=str,
        )[:15000]
        response = get_llm().invoke(
            [
                SystemMessage(content=ANSWER_SYSTEM),
                HumanMessage(
                    content=(
                        f"用户问题：{state['question']}\n\n"
                        f"最终 SQL：\n{state['sql']}\n\n"
                        f"查询结果（最多展示 {settings.row_limit} 行）：\n{rows_text}"
                    )
                ),
            ]
        )
        answer = _message_text(response).strip()

    return {
        "answer": answer,
        "messages": [AIMessage(content=answer)],
    }


def route_after_execute(
    state: SQLAgentState,
) -> Literal["repair_sql", "summarize_answer"]:
    if state.get("sql_error") and state.get("retry_count", 0) <= settings.max_sql_repairs:
        return "repair_sql"
    return "summarize_answer"


def build_graph():
    builder = StateGraph(SQLAgentState)

    builder.add_node("prepare", prepare_node)
    builder.add_node("select_tables", select_tables_node)
    builder.add_node("generate_sql", generate_sql_node)
    builder.add_node("execute_sql", execute_sql_node)
    builder.add_node("repair_sql", repair_sql_node)
    builder.add_node("summarize_answer", summarize_answer_node)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "select_tables")
    builder.add_edge("select_tables", "generate_sql")
    builder.add_edge("generate_sql", "execute_sql")
    builder.add_conditional_edges(
        "execute_sql",
        route_after_execute,
        {
            "repair_sql": "repair_sql",
            "summarize_answer": "summarize_answer",
        },
    )
    builder.add_edge("repair_sql", "execute_sql")
    builder.add_edge("summarize_answer", END)

    return builder.compile(checkpointer=InMemorySaver())


sql_agent = build_graph()


def make_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def agent_input(question: str) -> dict[str, Any]:
    return {
        "question": question,
        "messages": [HumanMessage(content=question)],
    }


def final_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer": state.get("answer", ""),
        "sql": state.get("sql", ""),
        "selected_tables": state.get("selected_tables", []),
        "selection_reason": state.get("selection_reason", ""),
        "sql_explanation": state.get("sql_explanation", ""),
        "columns": state.get("columns", []),
        "rows": state.get("rows", []),
        "sql_error": state.get("sql_error", ""),
        "retry_count": state.get("retry_count", 0),
    }
