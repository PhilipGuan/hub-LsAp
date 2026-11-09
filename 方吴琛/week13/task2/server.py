"""Chinook SQL Agent 的 Web 前端服务。

复用 task1 的 ``SQLAgentWorkflow``：把命令行一次性的 ``run(question)`` 暴露成
一个 HTTP 接口，前端网页以对话形式调用。

    GET  /            -> 聊天页面（static/index.html）
    GET  /health      -> 健康检查
    POST /api/query   -> {"question": "..."} -> WorkflowOutput JSON

运行：

    # 先准备好 task1 所需的环境变量（与命令行一致）
    export OPENAI_API_KEY=<你的 DeepSeek Key>
    export OPENAI_BASE_URL=https://api.deepseek.com   # 不要带 /anthropic

    cd task2
    python server.py            # 默认监听 http://127.0.0.1:8000

说明：
- task1 的 ``src`` 被加入 ``sys.path``，无需单独安装 task1。
- 共享一个 ``SQLAgentWorkflow`` 实例：图只编译一次，DB catalog / executor 都是
  只读的，可安全并发；每个问题仍独立问答（agent 本身不保留多轮上下文）。
- 对话历史只在前端维护，用于展示；后端每条消息都是独立查询。
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 把 task1 的 src 加入 import 路径：task2 与 task1 同级（week13/task1、week13/task2）。
_TASK1_SRC = Path(__file__).resolve().parent.parent / "task1" / "src"
if str(_TASK1_SRC) not in sys.path:
    sys.path.insert(0, str(_TASK1_SRC))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from sql_agent.graph import SQLAgentWorkflow  # noqa: E402

logger = logging.getLogger("sql_agent.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 模块级单例：图编译一次，DB 句柄只读复用。在缺少 API key 时也允许启动，
# 这样前端页面可以打开；真正查询时再给出清晰错误。
workflow = SQLAgentWorkflow()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=os.getenv("SQL_AGENT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    yield


app = FastAPI(title="Chinook SQL Agent Web", version="0.1.0", lifespan=lifespan)


class QueryRequest(BaseModel):
    question: str


class HealthResponse(BaseModel):
    status: str
    model: str
    base_url: str
    api_key_configured: bool


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    from sql_agent.config import settings

    return HealthResponse(
        status="ok",
        model=settings.primary_model,
        base_url=settings.base_url,
        api_key_configured=bool(os.getenv("OPENAI_API_KEY")),
    )


@app.post("/api/query")
async def query(req: QueryRequest) -> JSONResponse:
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不能为空")

    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="服务器未配置 OPENAI_API_KEY，无法调用模型。请在启动服务前设置。",
        )

    try:
        output = await workflow.run(question)
    except Exception as exc:  # noqa: BLE001 - 前端需要稳定的错误结构
        logger.exception("workflow run failed")
        raise HTTPException(status_code=500, detail=f"查询失败：{exc}") from exc

    return JSONResponse(output.model_dump(mode="json"))


# 静态资源：挂载在根路径下（/index.html、/style.css、/app.js）。
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("SQL_AGENT_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("SQL_AGENT_WEB_PORT", "8001"))
    uvicorn.run("server:app", host=host, port=port, reload=False)
