"""作业1 · 接口层：FastAPI 应用工厂。

两个后端共用同一套路由（/chat、/sessions、/health），
只注入不同的框架名称与流水线函数（run_pipeline），
保证两框架的 API 行为完全一致。
"""
from __future__ import annotations

import inspect
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from core.entities import (
    ChatRequest,
    ChatResponse,
    Session,
    SessionDetail,
)
from storage import repository as repo
from storage.database import init_db


def create_app(framework: str, run_pipeline, run_pipeline_stream) -> FastAPI:
    """创建一个后端应用。

    run_pipeline 既可以是同步函数（langgraph_flow），
    也可以是异步函数（agents_flow），内部自动适配；
    run_pipeline_stream 为两框架统一的异步生成器（SSE 事件流）。
    """
    app = FastAPI(title=f"{framework} 情感助手后端")
    init_db()

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        if not req.text.strip():
            raise HTTPException(status_code=400, detail="text 不能为空")

        outcome = run_pipeline(req.session_id, req.text)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return outcome

    @app.post("/sessions", response_model=Session, status_code=201)
    def create_session() -> Session:
        """立即创建空会话（前端「新建会话」按钮调用）。"""
        return repo.create_session()

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest):
        """流式对话（SSE 协议）。

        事件流：sentiment（识别结果）→ delta×N（回应增量）→ done（完整结果）
        """
        if not req.text.strip():
            raise HTTPException(status_code=400, detail="text 不能为空")

        gen = run_pipeline_stream(req.session_id, req.text)

        async def sse_events():
            async for event in gen:
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            sse_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/sessions", response_model=list[Session])
    def sessions() -> list[Session]:
        return repo.list_sessions()

    @app.get("/sessions/{session_id}", response_model=SessionDetail)
    def session_detail(session_id: str) -> SessionDetail:
        session = repo.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return SessionDetail(
            session=session,
            messages=repo.get_all_messages(session_id),
            memories=repo.get_all_memories(session_id),
        )

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> dict:
        if not repo.delete_session(session_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"status": "deleted", "session_id": session_id}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "framework": framework}

    return app