"""FastAPI 入口：前后端不分离，直接挂载 layui 静态页并提供查询接口。

启动方式（在仓库根目录执行，默认监听 127.0.0.1:8000）：

    PYTHONPATH=src uvicorn sql_agent.api:app

开发时可以加上 --reload；需要改地址/端口时使用 uvicorn 的 --host / --port
（或对应的 UVICORN_HOST / UVICORN_PORT 环境变量）：

    PYTHONPATH=src uvicorn sql_agent.api:app --reload --host 0.0.0.0 --port 8000

浏览器打开 http://127.0.0.1:8000/ 即可使用。接口地址、API Key 和模型名
可在页面右上角“接口设置”中临时覆盖；API Key 只保存在当前浏览器标签页，
不会写入日志，也不会出现在接口响应里。
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import settings
from .llm import LLMRuntimeConfig, use_llm_config
from .logging_config import configure_logging
from .workflow import SQLAgentWorkflow

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_FILE = WEB_DIR / "index.html"

EXAMPLE_QUESTIONS = [
    "总共有多少张专辑？",
    "销售额最高的 5 位客户是谁？",
    "每个流派的歌曲数量分别是多少？",
    "2010 年每个月的发票总金额是多少？",
    "哪些艺术家拥有的曲目数量最多？",
]


class LLMConfigPayload(BaseModel):
    """页面上可选的模型配置覆盖项。空值表示使用服务端默认配置。"""

    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=120)
    summary_model: str | None = Field(default=None, max_length=120)

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("接口地址必须是 http(s):// 开头的完整 URL")
        return value

    @field_validator("api_key", "model", "summary_model")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    llm: LLMConfigPayload | None = None

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请输入查询问题")
        return value


_workflow: SQLAgentWorkflow | None = None
_workflow_lock = threading.Lock()


def _get_workflow() -> SQLAgentWorkflow:
    """进程内复用同一个工作流和已解析的 schema 目录。"""

    global _workflow
    if _workflow is None:
        with _workflow_lock:
            if _workflow is None:
                _workflow = SQLAgentWorkflow()
    return _workflow


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(os.getenv("SQL_AGENT_LOG_LEVEL", "INFO"))
    workflow = _get_workflow()
    logger.info(
        "stage=web_ready tables=%d db=%s model=%s summary_model=%s",
        len(workflow.catalog.table_names),
        workflow.catalog.db_path,
        settings.primary_model,
        settings.summary_model,
    )
    yield


app = FastAPI(
    title="Chinook SQL Agent",
    description="自然语言查询 Chinook 数据库",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path == "/":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health", summary="健康检查")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config", summary="页面初始化配置")
async def get_config() -> dict:
    """返回默认模型配置和数据库概览；永远不会返回 API Key。"""

    workflow = _get_workflow()
    return {
        "base_url": settings.base_url,
        "primary_model": settings.primary_model,
        "summary_model": settings.summary_model,
        "has_server_api_key": bool(os.getenv("OPENAI_API_KEY")),
        "example_questions": EXAMPLE_QUESTIONS,
        "database": {
            "file": workflow.catalog.db_path.name,
            "tables": workflow.catalog.table_overview(),
        },
    }


@app.post("/api/query", summary="自然语言查询")
async def run_query(payload: QueryRequest) -> dict:
    llm = payload.llm or LLMConfigPayload()
    if not (llm.api_key or os.getenv("OPENAI_API_KEY")):
        raise HTTPException(
            status_code=400,
            detail=(
                "未配置 API Key：请在页面右上角“接口设置”中填写，"
                "或为服务端设置 OPENAI_API_KEY 环境变量。"
            ),
        )

    runtime = LLMRuntimeConfig(
        base_url=llm.base_url,
        api_key=llm.api_key,
        primary_model=llm.model,
        summary_model=llm.summary_model,
    )
    workflow = _get_workflow()
    with use_llm_config(runtime):
        output = await workflow.run(payload.question)
    return output.model_dump()


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_FILE)


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
