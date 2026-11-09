# -*- coding: utf-8 -*-
"""FastAPI 接口：4 个端点 + 后台研究任务。

规格见 README.md / 任务说明书.md（T10）。
启动（在项目根目录，以便加载 .env）：
    bash start.sh
    # 或：uvicorn backend.app:app --reload --port 8000
    # 或：python -m backend.app
接口：
    GET  /health              健康检查
    POST /api/research        发起一次研究（202，立即返回 research_id）
    GET  /api/research/{rid}  查询研究状态 / 结果（轮询用）
    GET  /api/research        研究记录列表
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config, storage
from .models import ResearchRecord, ResearchRequest
from .research import run_research

logger = logging.getLogger(__name__)

# 持有后台任务引用，避免任务被垃圾回收（事件循环只保留弱引用）
_BACKGROUND_TASKS: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时打开日志、确保数据目录存在；关闭前等待后台任务结束。"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    config.ensure_data_dir()
    logger.info("深度研究助手启动：数据目录 %s", config.DATA_DIR)
    yield
    if _BACKGROUND_TASKS:
        logger.info("等待 %d 个后台研究任务结束…", len(_BACKGROUND_TASKS))
        await asyncio.gather(*_BACKGROUND_TASKS, return_exceptions=True)
    logger.info("深度研究助手已停止")


app = FastAPI(title="深度研究助手 API", version="1.0.0", lifespan=lifespan)

# 允许前端跨域调用（来源由 .env 的 FRONTEND_ORIGIN 控制）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    """请求体校验失败（如空 topic）统一返回 400，而非 FastAPI 默认的 422。"""
    errors = [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=400, content={"detail": "请求参数不合法", "errors": errors})


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查。"""
    return {"status": "ok"}


@app.post("/api/research", status_code=202)
async def start_research(req: ResearchRequest) -> dict[str, str]:
    """发起一次研究：立即返回 research_id，研究循环在后台任务里执行。"""
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic 不能为空")

    rid = uuid4().hex[:12]
    storage.create(rid, topic)
    task = asyncio.create_task(run_research(rid, topic))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)  # 完成后自动移出，避免集合膨胀
    logger.info("收到研究请求: rid=%s topic=%r", rid, topic)
    return {"research_id": rid, "status": "pending"}


@app.get("/api/research/{rid}", response_model=ResearchRecord)
async def get_research(rid: str) -> ResearchRecord:
    """按 research_id 查询研究状态 / 结果（轮询用）。"""
    rec = storage.get(rid)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"研究记录不存在: {rid}")
    return rec


@app.get("/api/research", response_model=list[ResearchRecord])
async def list_research() -> list[ResearchRecord]:
    """返回全部研究记录（按创建时间排序）。"""
    return storage.list_all()


# ------------------------------------------------------------------ 自检 demo
if __name__ == "__main__":
    import socket

    import uvicorn

    print("深度研究助手 API —— 已注册路由：")
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods:
            label = ",".join(sorted(methods - {"HEAD", "OPTIONS"}))
            print(f"  {label:6s} {route.path}")

    port = 8000
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:  # 端口已被占用
            port = 8001
            print(f"端口 8000 已被占用，改用 {port}")

    print(f"启动开发服务器 http://127.0.0.1:{port} （Ctrl+C 停止）")
    uvicorn.run(app, host="127.0.0.1", port=port)
