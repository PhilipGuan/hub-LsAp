"""FastAPI 前端服务：网页对话式查询 Chinook 数据库。

与 CLI（``cli.py``）共用同一套 ``SQLAgentWorkflow``／``build_workflow``，
不重复编排逻辑：

- 启动时（``lifespan``）构建一次全局 ``SQLAgentWorkflow`` 单例并复用，
  避免每个请求都重新解析 ``SchemaCatalog``（解析一次 Chinook 库约 0.8s）。
- ``POST /api/query`` 接收一条自然语言问题，返回与 CLI 相同结构的
  ``WorkflowOutput`` JSON（question/answer/sql/tables/row_count/...）。
- ``GET /`` 返回 ``static/index.html``，一个极简的单页对话界面，用
  ``fetch`` 调用上面的接口，不引入前端构建链路。
- 鉴权：当前未加任何身份验证，仅适合本机/内网演示；对外部署前需要加
  认证与速率限制（见 README 中的安全提示）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .logging_config import configure_logging
from .models import WorkflowOutput
from .workflow import SQLAgentWorkflow, build_workflow

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

_workflow: SQLAgentWorkflow | None = None


def get_workflow() -> SQLAgentWorkflow:
    """返回启动时构建好的全局 workflow 实例。"""

    if _workflow is None:
        raise RuntimeError("workflow 尚未初始化，应用未正常启动")
    return _workflow


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _workflow
    configure_logging()
    logger.info("stage=startup action=build_workflow")
    _workflow = build_workflow()
    try:
        yield
    finally:
        _workflow = None


app = FastAPI(
    title="Chinook SQL Agent",
    description="自然语言查询 Chinook 数据库（LangGraph 版本）",
    version="0.1.0",
    lifespan=lifespan,
)

# 本地/内网演示场景下放开跨域，便于前端单独部署调试；对外部署需按实际来源收窄。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="自然语言查询问题")


@app.post("/api/query", response_model=WorkflowOutput)
async def query(request: QueryRequest) -> WorkflowOutput:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    workflow = get_workflow()
    try:
        return await workflow.run(question)
    except Exception as exc:  # noqa: BLE001 - 对外统一返回 500，详细信息走日志
        logger.exception("stage=query_failed question=%s", question)
        raise HTTPException(status_code=500, detail=f"查询执行失败：{exc}") from exc


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
