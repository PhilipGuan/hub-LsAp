"""FastAPI 后端：把 SQL Agent 包装成对话式 HTTP 接口。

启动：
    python -m sql_agent_langgraph.api
或：
    uvicorn sql_agent_langgraph.api:app --reload --port 8000

接口文档：http://127.0.0.1:8000/docs

如果已经执行过 `frontend` 目录下的 npm run build，后端会自动把
`week13/frontend/dist` 挂载到 `/`，直接访问 http://127.0.0.1:8000 即可。

说明：会话历史保存在进程内存中，用于前端展示对话；当前每一轮问题仍由
SQL Agent 独立回答。多进程部署时可把 app.state.sessions 换成 Redis。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import settings
from .logging_config import configure_logging
from .models import WorkflowOutput
from .workflow import SQLAgentWorkflow

MAX_SESSIONS = int(os.getenv("SQL_AGENT_MAX_SESSIONS", "500"))
MAX_TURNS_PER_SESSION = int(os.getenv("SQL_AGENT_MAX_TURNS", "100"))
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


class ChatRequest(BaseModel):
    """前端一次发送的问题。session_id 不传则自动创建。"""

    question: str = Field(min_length=1, max_length=4000, description="用户的自然语言问题")
    session_id: str | None = Field(
        default=None, max_length=64, description="对话会话 ID，首轮可不传"
    )

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question 不能为空")
        return value


class ChatResponse(BaseModel):
    session_id: str
    elapsed_ms: int
    result: WorkflowOutput


class Turn(BaseModel):
    question: str
    created_at: datetime
    result: WorkflowOutput


class SessionHistory(BaseModel):
    session_id: str
    turns: list[Turn]


def _cors_origins() -> list[str]:
    raw = os.getenv("SQL_AGENT_CORS_ORIGINS", "*")
    return [item.strip() for item in raw.split(",") if item.strip()] or ["*"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(os.getenv("SQL_AGENT_LOG_LEVEL", "INFO"))
    app.state.workflow = SQLAgentWorkflow()
    app.state.sessions = OrderedDict()
    app.state.session_lock = asyncio.Lock()
    yield

    workflow = getattr(app.state, "workflow", None)
    catalog = getattr(workflow, "catalog", None)
    if catalog is not None:
        catalog.close()
    app.state.sessions.clear()


def create_app() -> FastAPI:
    app = FastAPI(
        title="SQL Agent API",
        version="1.0.0",
        description="用自然语言查询 Chinook 数据库的对话式后端。",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", summary="健康检查")
    async def health() -> dict:
        return {
            "status": "ok",
            "model": settings.primary_model,
            "database": str(settings.db_path),
            "database_exists": settings.db_path.exists(),
        }

    @app.post("/api/chat", response_model=ChatResponse, summary="提问并执行 SQL Agent")
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        workflow: SQLAgentWorkflow = request.app.state.workflow
        session_id = payload.session_id or uuid.uuid4().hex

        # 先登记会话，避免并发的首轮请求各自创建不同会话。
        async with request.app.state.session_lock:
            sessions: OrderedDict[str, list[Turn]] = request.app.state.sessions
            turns = sessions.setdefault(session_id, [])
            sessions.move_to_end(session_id)
            if len(sessions) > MAX_SESSIONS:
                sessions.popitem(last=False)

        started = time.perf_counter()
        result = await workflow.run(payload.question)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        async with request.app.state.session_lock:
            turns = request.app.state.sessions.setdefault(session_id, [])
            turns.append(
                Turn(
                    question=payload.question,
                    created_at=datetime.now(timezone.utc),
                    result=result,
                )
            )
            del turns[:-MAX_TURNS_PER_SESSION]

        return ChatResponse(
            session_id=session_id,
            elapsed_ms=elapsed_ms,
            result=result,
        )

    @app.get(
        "/api/sessions/{session_id}",
        response_model=SessionHistory,
        summary="查看某个会话的历史消息",
    )
    async def get_session(session_id: str, request: Request) -> SessionHistory:
        async with request.app.state.session_lock:
            sessions: OrderedDict[str, list[Turn]] = request.app.state.sessions
            turns = sessions.get(session_id)
            if turns is None:
                raise HTTPException(status_code=404, detail="会话不存在")
            sessions.move_to_end(session_id)
            return SessionHistory(session_id=session_id, turns=list(turns))

    @app.delete(
        "/api/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
        summary="删除会话",
    )
    async def delete_session(session_id: str, request: Request) -> Response:
        async with request.app.state.session_lock:
            if request.app.state.sessions.pop(session_id, None) is None:
                raise HTTPException(status_code=404, detail="会话不存在")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # 构建过前端后（npm run build），后端也可以直接托管 dist。
    if FRONTEND_DIST.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=FRONTEND_DIST, html=True),
            name="frontend",
        )

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "sql_agent_langgraph.api:app",
        host=os.getenv("SQL_AGENT_API_HOST", "127.0.0.1"),
        port=int(os.getenv("SQL_AGENT_API_PORT", "8000")),
        reload=os.getenv("SQL_AGENT_API_RELOAD", "").lower() in {"1", "true", "yes"},
    )


if __name__ == "__main__":
    main()
