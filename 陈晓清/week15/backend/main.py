"""FastAPI 后端入口。

运行：uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
模块自测：python -m backend.main
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.kafka_bridge import KafkaBridge, KafkaBridgeError, RequestTimeoutError
from shared.schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)
bridge = KafkaBridge()

# 镜像构建时前端产物会放到这里；本地开发时通常不存在（前端由 Vite 提供）
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bridge.start()
    try:
        yield
    finally:
        await bridge.stop()


app = FastAPI(title="情感支持对话 Demo", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    try:
        return await bridge.ask(request.message)
    except RequestTimeoutError as exc:
        raise HTTPException(status_code=504, detail="处理超时，请稍后重试") from exc
    except KafkaBridgeError as exc:
        logger.exception("处理聊天请求失败")
        raise HTTPException(status_code=502, detail="处理失败，请稍后重试") from exc


# 必须放在所有 API 路由之后：请求按注册顺序匹配，挂载到 "/" 才能正确回落到前端
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")


def main() -> None:
    """自测：走 lifespan 启动/关闭 bridge，并检查 /health。"""
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        print("GET /health:", client.get("/health").json())


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    main()
