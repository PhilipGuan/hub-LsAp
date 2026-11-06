"""SQL Agent 的 FastAPI 后端：给 Vue 前端提供 /api/chat 接口。

启动：

    cd 张利强/week13/作业二
    /opt/anaconda3/envs/py312/bin/python -m uvicorn server:app --reload --port 8000

前端开发模式（Vite）会把 /api 代理到 8000 端口。
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sql_agent import DB_PATH, DEFAULT_MODEL, DBParser, SQLAgentWorkflow, create_llm

app = FastAPI(title="Chinook SQL Agent", version="1.0.0")

# 本地开发时允许 Vite dev server 访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 启动时解析一次数据库，复用 agent 与连接池
parser = DBParser(f"sqlite:///{DB_PATH}")
workflow = SQLAgentWorkflow(parser, create_llm())


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000, description="用户自然语言问题")


class ChatResponse(BaseModel):
    answer: str
    table: str | None = None
    sql: str | None = None
    columns: list[str] = []
    rows: list[list[str]] = []
    elapsed_ms: int = 0


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": DEFAULT_MODEL}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    started = time.perf_counter()
    try:
        result = workflow.run(request.message)
    except Exception as exc:  # noqa: BLE001 - 把 Agent 错误转成前端可读信息
        raise HTTPException(status_code=502, detail=f"Agent 调用失败：{exc}") from exc

    return ChatResponse(
        **result,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


# 若已经执行过 npm run build，则直接由 FastAPI 托管前端
DIST_DIR = Path(__file__).resolve().parent / "web" / "dist"
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="frontend")
