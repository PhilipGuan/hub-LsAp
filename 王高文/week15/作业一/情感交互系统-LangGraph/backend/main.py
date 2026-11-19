"""提供非流式聊天 API。"""

import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from backend.database.storage import ChatStorage
from backend.models.schemas import ChatRequest, ChatResponse, HistoryMessage, NonBlankText
from backend.services.session_service import SessionService


_LOGGER = logging.getLogger(__name__)
_STORAGE_ERROR = {status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "会话存储暂时不可用"}}


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.session_service = SessionService(ChatStorage())
    yield


app = FastAPI(title="多 Agent 情感交互系统", lifespan=_lifespan)


@app.exception_handler(sqlite3.Error)
async def _storage_error(_request: Request, error: sqlite3.Error) -> JSONResponse:
    _LOGGER.warning("SQLite 会话存储失败（%s）", type(error).__name__)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "暂时无法读取或保存会话，请稍后再试。"},
    )


@app.post(
    "/chat",
    response_model_exclude_none=True,
    responses=_STORAGE_ERROR,
)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    """完成一轮情绪识别与情感回应。"""
    service: SessionService = request.app.state.session_service
    return await service.chat(payload)


@app.get("/history", responses=_STORAGE_ERROR)
async def history(session_id: NonBlankText, request: Request) -> list[HistoryMessage]:
    """按时间顺序返回指定会话的历史消息。"""
    service: SessionService = request.app.state.session_service
    return await service.load_history(session_id)
