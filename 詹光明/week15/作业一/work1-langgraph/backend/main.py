"""后端进程：FastAPI 入口。

启动方式（在 work1-langgraph 目录下）：

    uvicorn backend.main:app --host 127.0.0.1 --port 8000

或者：

    python -m backend.main
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .models import ChatRequest, ChatResponse, HistoryResponse
from .service import ChatService

logger = logging.getLogger(__name__)

service = ChatService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    settings = get_settings()
    logger.info(
        "backend started model=%s base_url=%s api_key_configured=%s",
        settings.model,
        settings.base_url,
        settings.api_key_configured,
    )
    if not settings.api_key_configured:
        logger.warning(
            "OPENAI_API_KEY 未配置：请在项目根目录的 .env 中填写，"
            "否则情感识别与回复会降级为兜底文案。"
        )
    yield
    logger.info("backend stopped")


app = FastAPI(
    title="情绪陪伴助手",
    description="进程1 情感识别 + 进程2 鼓励/排解，由 LangGraph 串联。",
    version="1.0.0",
    lifespan=lifespan,
)

# 本地开发前端在 8501 端口，Streamlit 通过服务端请求访问后端；
# 保留 CORS 配置，便于以后改成浏览器直连。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "model": settings.model,
        "base_url": settings.base_url,
        "api_key_configured": settings.api_key_configured,
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        return await service.chat(request.message, request.thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 对外只暴露稳定错误信息
        logger.exception("chat failed thread=%s", request.thread_id)
        raise HTTPException(
            status_code=500,
            detail="对话处理失败，请检查后端日志与模型配置。",
        ) from exc


@app.get("/api/history", response_model=HistoryResponse)
async def history(
    thread_id: str = Query(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
) -> HistoryResponse:
    return await service.history(thread_id)


@app.delete("/api/thread/{thread_id}")
async def delete_thread(thread_id: str) -> dict:
    await service.delete_thread(thread_id)
    return {"deleted": thread_id}


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        app,
        host=settings.backend_host,
        port=settings.backend_port,
    )
