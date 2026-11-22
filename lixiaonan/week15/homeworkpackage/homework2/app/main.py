"""后端入口（设计文档 §4.1）。

- GET  /            返回前端静态页面
- GET  /api/health  健康检查：当前 engine 与模型名
- POST /api/chat    主接口：跑完整流水线（进程1 → 条件路由 → 进程2）

启动方式：uvicorn main:app --port 8000   或   python main.py
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from config import MODEL_NAME, PIPELINE_ENGINE, PORT, validate_settings
from pipelines import get_pipeline
from schemas import ChatRequest, PipelineResult

logger = logging.getLogger("homework1")
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_settings()  # 启动时校验必填配置（设计文档 §7）
    logger.info("启动完成：engine=%s, model=%s", PIPELINE_ENGINE, MODEL_NAME)
    yield


app = FastAPI(title="情感陪伴助手", lifespan=lifespan)

# 按 PIPELINE_ENGINE 选择实现（langgraph | openai-agents），前端零改动
run_pipeline = get_pipeline()


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "engine": PIPELINE_ENGINE, "model": MODEL_NAME}


@app.post("/api/chat", response_model=PipelineResult)
async def chat(req: ChatRequest):
    try:
        return await run_pipeline(req.text)
    except Exception as e:  # noqa: BLE001 —— 模型侧失败统一映射 502（设计文档 §7）
        logger.exception("pipeline 执行失败")
        raise HTTPException(status_code=502, detail=f"模型调用失败：{e}") from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=PORT)
