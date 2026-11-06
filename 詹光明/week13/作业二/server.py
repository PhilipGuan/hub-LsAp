"""Chinook SQL 助手 —— week13 作业二：为 week13/作业一 的 LangGraph 后端加网页前端。

启动：
    pip install -r requirements.txt
    python server.py
    # 或：uvicorn server:app --host 127.0.0.1 --port 8000

接口：
    GET  /                  网页（web/index.html）
    GET  /api/health        服务状态、模型与数据库可用性
    GET  /api/config        前端展示所需的模型、库表、示例问题
    POST /api/query         单次查询，返回完整 JSON
    POST /api/query/stream  单次查询，SSE 推送每个图节点的进度与最终结果

后端直接复用 ../作业一 的 sql_agent 包（不复制代码）：
`SQLAgentGraph` 构建状态图，`astream(stream_mode="updates")` 把
选表 / 生成 / 校验执行 / 改写 / 汇总 的每一步转成前端进度事件。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
BACKEND_DIR = (BASE_DIR.parent / "作业一").resolve()
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sql_agent.config import settings  # noqa: E402
from sql_agent.graph import SQLAgentGraph  # noqa: E402
from sql_agent.models import WorkflowOutput  # noqa: E402
from sql_agent.workflow import SQLAgentWorkflow  # noqa: E402

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
logging.basicConfig(
    level=os.getenv("SQL_AGENT_LOG_LEVEL", "INFO").upper(),
    format=LOG_FORMAT,
    datefmt="%Y-%m-%d %H:%M:%S",
)
for noisy in ("httpx", "httpcore", "openai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger("sql_agent.web")

MAX_QUESTION_CHARS = 2000
EXAMPLES = [
    "销售额最高的前 5 位艺术家是谁？",
    "每个国家的客户数量，按数量降序",
    "2010 年每个月的总销售额",
    "哪些流派（Genre）的歌曲最多？",
    "平均时长最长的 5 张专辑",
]

# 启动时编译一次状态图；失败原因留给 /api/health 和查询接口报告。
STARTUP_ERROR: str | None = None
try:
    BUILDER = SQLAgentGraph()
    GRAPH = BUILDER.build()
    WORKFLOW = SQLAgentWorkflow(compiled_graph=GRAPH)
except Exception as exc:  # noqa: BLE001 - 服务需要先起来再报告问题
    BUILDER = None
    GRAPH = None
    WORKFLOW = None
    STARTUP_ERROR = str(exc)
    logger.exception("后端初始化失败")

logger.info("复用后端包：%s", BACKEND_DIR)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


def _ensure_ready() -> None:
    if STARTUP_ERROR:
        raise HTTPException(status_code=503, detail=f"后端初始化失败：{STARTUP_ERROR}")
    if not settings.api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "后端未配置 OPENAI_API_KEY。请在 week13/作业一/.env 或环境变量中设置后重启。"
            ),
        )


def _clean_question(payload: QueryRequest) -> str:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    return question


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _progress(node: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """把一个节点的状态增量翻译成前端进度事件。"""

    if node == "select_tables":
        selection = changes.get("selection")
        feedback = changes.get("selection_feedback") or ""
        if selection is not None and selection.needs_clarification:
            detail = selection.clarification_question or "需要更多信息"
        elif feedback:
            detail = feedback
        else:
            detail = "、".join(changes.get("tables") or []) or "未选择表"
        return {
            "stage": node,
            "label": "选择相关表",
            "status": "done",
            "detail": detail,
            "tables": changes.get("tables") or [],
        }

    if node == "generate_sql":
        sql = " ".join(str(changes.get("sql") or "").split())
        return {
            "stage": node,
            "label": "生成 SQL",
            "status": "done",
            "detail": sql[:160] + ("…" if len(sql) > 160 else ""),
        }

    if node == "validate_execute":
        execution = changes.get("execution")
        validation = changes.get("validation")
        if execution is not None:
            detail = f"返回 {execution.row_count} 行" + (
                "（已截断）" if execution.truncated else ""
            )
            return {
                "stage": node,
                "label": "校验并执行",
                "status": "done",
                "detail": detail,
            }
        return {
            "stage": node,
            "label": "校验并执行",
            "status": "error",
            "detail": (validation.message if validation else "执行失败"),
        }

    if node == "rewrite_sql":
        if changes.get("answer_override"):
            return {
                "stage": node,
                "label": "修复 SQL",
                "status": "error",
                "detail": "模型判断当前数据库无法回答该问题",
            }
        return {
            "stage": node,
            "label": "修复 SQL",
            "status": "done",
            "detail": f"第 {changes.get('repair_attempts', 1)} 次改写",
        }

    if node == "summarize":
        return {
            "stage": node,
            "label": "汇总答案",
            "status": "done",
            "detail": "已根据查询结果生成回答",
        }

    return None


async def _query_events(question: str) -> AsyncIterator[str]:
    """SSE：逐节点推送进度，finalize 时推送完整结果。"""

    started = time.perf_counter()
    request_id = uuid.uuid4().hex[:8]
    logger.info("[%s] 查询开始 question=%s", request_id, question)
    try:
        assert BUILDER is not None and GRAPH is not None
        async for chunk in GRAPH.astream(
            {"question": question, "trace": []},
            config={"recursion_limit": BUILDER.recursion_limit},
            stream_mode="updates",
        ):
            for node, changes in chunk.items():
                event = _progress(node, changes)
                if event is not None:
                    yield _sse("progress", event)
                if node == "finalize":
                    output = changes.get("output")
                    if isinstance(output, WorkflowOutput):
                        yield _sse(
                            "result",
                            {
                                "request_id": request_id,
                                "output": output.model_dump(),
                                "elapsed_ms": int(
                                    (time.perf_counter() - started) * 1000
                                ),
                            },
                        )
    except asyncio.CancelledError:
        logger.info("[%s] 客户端断开", request_id)
        raise
    except Exception as exc:  # noqa: BLE001 - 以事件形式返回给前端
        logger.exception("[%s] 查询失败", request_id)
        yield _sse("error", {"message": str(exc), "request_id": request_id})
    else:
        logger.info(
            "[%s] 查询结束 elapsed=%dms",
            request_id,
            int((time.perf_counter() - started) * 1000),
        )
        yield _sse(
            "done",
            {
                "request_id": request_id,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            },
        )


app = FastAPI(
    title="Chinook SQL 助手",
    description="自然语言查询 Chinook 数据库（LangGraph SQL Agent）。",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def index():
    index_file = WEB_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    raise HTTPException(status_code=404, detail="缺少 web/index.html")


@app.get("/api/health")
def health():
    db_path = settings.db_path
    ready = STARTUP_ERROR is None and bool(settings.api_key) and db_path.exists()
    if STARTUP_ERROR:
        status = "error"
    elif not settings.api_key:
        status = "missing_key"
    elif not db_path.exists():
        status = "missing_db"
    else:
        status = "ok"
    return {
        "status": status,
        "ready": ready,
        "model": settings.primary_model,
        "summary_model": settings.summary_model,
        "base_url": settings.base_url,
        "database": db_path.name,
        "database_exists": db_path.exists(),
        "api_key_configured": bool(settings.api_key),
        "error": STARTUP_ERROR,
    }


@app.get("/api/config")
def config():
    tables = BUILDER.catalog.table_names if BUILDER is not None else []
    return {
        "model": settings.primary_model,
        "summary_model": settings.summary_model,
        "database": settings.db_path.name,
        "tables": tables,
        "max_repair_attempts": settings.max_repair_attempts,
        "max_table_attempts": settings.max_table_attempts,
        "max_rows": settings.max_result_rows,
        "api_key_configured": bool(settings.api_key),
        "examples": EXAMPLES,
    }


@app.post("/api/query")
async def query(payload: QueryRequest):
    """非流式查询：一次返回完整结果，方便脚本调用。"""

    question = _clean_question(payload)
    _ensure_ready()
    assert WORKFLOW is not None
    started = time.perf_counter()
    output = await WORKFLOW.run(question)
    return {
        "question": question,
        "output": output.model_dump(),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


@app.post("/api/query/stream")
async def query_stream(payload: QueryRequest):
    """流式查询：SSE 事件 progress / result / error / done。"""

    question = _clean_question(payload)
    _ensure_ready()
    return StreamingResponse(
        _query_events(question),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host=os.getenv("SQL_AGENT_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("SQL_AGENT_WEB_PORT", "8000")),
    )
