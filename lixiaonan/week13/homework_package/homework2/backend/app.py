"""FastAPI 后端：把 sql_agent_langgraph（作业1）暴露为 Web 服务。

- POST /api/query          非流式查询，返回 WorkflowOutput JSON
- POST /api/query/stream   SSE 流式查询：start -> node×N（逐节点状态更新）-> final | error
- GET  /api/health         健康检查
- /                        托管 frontend/ 静态页面

API key 仍只从环境变量读取（OPENAI_API_KEY）；工作流懒加载，
缺 key 时服务可以启动，首个查询会收到友好的错误事件。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sql_agent_langgraph.config import settings
from sql_agent_langgraph.logging_config import configure_logging
from sql_agent_langgraph.models import WorkflowOutput

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

app = FastAPI(title="SQL Agent Web（LangGraph）", version="0.1.0")

_workflow = None
_workflow_lock = threading.Lock()


def get_workflow():
    """懒加载 SQLAgentGraph（进程级单例）。

    catalog 首次解析约 0.1s，LLM 构建需要 OPENAI_API_KEY；
    缺 key 时抛 RuntimeError，由端点转成友好错误。
    """

    global _workflow
    if _workflow is None:
        with _workflow_lock:
            if _workflow is None:
                from sql_agent_langgraph.graph import SQLAgentGraph

                _workflow = SQLAgentGraph()
                logger.info("workflow initialized db=%s", settings.db_path)
    return _workflow


class QueryRequest(BaseModel):
    question: str
    thread_id: str | None = None  # 同一线程可回放；不传则每次新建


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """兼容 Pydantic 对象与 dict（checkpoint 反序列化后可能是 dict）。"""

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _node_event(node: str, update: dict) -> dict:
    """从节点更新中提取前端需要的少量字段。"""

    info: dict[str, Any] = {}
    if node == "select_tables":
        selection = update.get("selection")
        info = {
            "tables": _get(selection, "tables", []),
            "confidence": _get(selection, "confidence"),
            "needs_clarification": _get(selection, "needs_clarification", False),
            "clarification_question": _get(selection, "clarification_question"),
        }
    elif node == "generate_sql":
        draft = update.get("draft")
        info = {
            "sql": _get(draft, "sql"),
            "explanation": _get(draft, "explanation"),
            "schema_chars": len(update.get("schema_context", "") or ""),
        }
    elif node == "validate_sql":
        validation = update.get("validation")
        info = {
            "ok": _get(validation, "ok"),
            "code": _get(validation, "code"),
            "message": _get(validation, "message"),
        }
    elif node == "execute_sql":
        execution = update.get("execution")
        if execution is not None:
            info = {
                "ok": True,
                "row_count": _get(execution, "row_count", 0),
                "truncated": _get(execution, "truncated", False),
                "columns": _get(execution, "columns", []),
            }
        else:
            failed = update.get("validation") or {}
            info = {
                "ok": False,
                "code": _get(failed, "code"),
                "message": _get(failed, "message"),
            }
    elif node == "rewrite_sql":
        draft = update.get("draft")
        info = {
            "sql": _get(draft, "sql"),
            "cannot_fix": _get(draft, "cannot_fix", False),
            "repair_attempts": update.get("repair_attempts"),
        }
    elif node == "summarize":
        answer = update.get("answer")
        info = {"answer": _get(answer, "answer")}
    elif node == "finalize":
        pass  # finalize 的完整结果由 final 事件下发

    return {"node": node, "info": info}


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_events(question: str, thread_id: str | None) -> Iterator[str]:
    """SSE 事件流：start -> node×N -> final；异常转 error 事件。"""

    yield _sse(
        "start",
        {"question": question, "thread_id": thread_id, "db": str(settings.db_path)},
    )
    try:
        workflow = get_workflow()
        config = {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}
        output: WorkflowOutput | None = None

        for chunk in workflow.graph.stream(
            {"question": question, "attempts": []}, config, stream_mode="updates"
        ):
            for node, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                yield _sse("node", _node_event(node, update))
                if node == "finalize":
                    candidate = update.get("output")
                    if isinstance(candidate, WorkflowOutput):
                        output = candidate

        if output is None:
            raise RuntimeError("图执行结束但未产生 output")
        yield _sse("final", {"output": output.model_dump(), "thread_id": thread_id})
    except Exception as exc:  # noqa: BLE001 - 任何异常都转为前端可见的 error 事件
        logger.exception("query failed question=%s", question)
        yield _sse("error", {"message": str(exc)})


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": settings.primary_model,
        "summary_model": settings.summary_model,
        "db": str(settings.db_path),
        "api_key_configured": bool(__import__("os").getenv("OPENAI_API_KEY")),
    }


@app.post("/api/query")
def query(req: QueryRequest) -> JSONResponse:
    """非流式查询：等整图跑完返回 WorkflowOutput。"""

    question = req.question.strip()
    if not question:
        return JSONResponse(
            WorkflowOutput(question="", answer="请输入要查询的问题。").model_dump()
        )
    try:
        output = get_workflow().run(question, thread_id=req.thread_id)
    except RuntimeError as exc:  # 缺 API key 等启动期错误
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(output.model_dump())


@app.post("/api/query/stream")
def query_stream(req: QueryRequest) -> StreamingResponse:
    """SSE 流式查询。前端用 fetch + ReadableStream 读取（避免 EventSource 重连重复执行）。"""

    question = req.question.strip()
    thread_id = req.thread_id or str(uuid.uuid4())
    if not question:
        return StreamingResponse(
            iter([_sse("error", {"message": "请输入要查询的问题。"})]),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _stream_events(question, thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# 静态前端（放在最后，避免吞掉 /api 路由）
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import os

    import uvicorn

    configure_logging(os.getenv("SQL_AGENT_LOG_LEVEL", "INFO"))
    uvicorn.run(
        app,
        host=os.getenv("SQL_AGENT_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("SQL_AGENT_WEB_PORT", "8000")),
    )
