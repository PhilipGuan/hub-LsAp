"""FastAPI 接口层。"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import config, storage
from backend.engine import run_research_safe
from backend.models import ResearchTask

logger = logging.getLogger(__name__)

app = FastAPI(title="深度研究助手", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = config.ROOT / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=1, description="研究主题")
    max_rounds: int = Field(default=0, ge=0, le=6, description="最大检索轮次，0 表示用配置默认值")
    fetch_full: bool = Field(default=True, description="是否抓取网页正文（默认开，更准但更慢）")


@app.get("/")
def index() -> Any:
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "深度研究助手 API", "docs": "/docs"}


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "llm_ready": bool(config.DEEPSEEK_API_KEY),
        "search_ready": bool(config.BOCHA_API_KEY),
        "max_rounds": config.RESEARCH_MAX_ROUNDS,
    }


@app.post("/api/research", status_code=202)
def create_research(req: ResearchRequest, bg: BackgroundTasks) -> dict:
    task = ResearchTask(topic=req.topic)
    task.status = "pending"
    storage.save(task)
    kw: dict[str, Any] = {"fetch_full": req.fetch_full}
    if req.max_rounds:
        kw["max_rounds"] = req.max_rounds
    bg.add_task(run_research_safe, req.topic, task.id, **kw)
    logger.info("创建研究任务 %s：%s", task.id, req.topic)
    return {"research_id": task.id, "status": "pending", "topic": req.topic}


@app.get("/api/research")
def list_research(limit: int = 20) -> dict:
    return {"items": storage.list_tasks(limit)}


@app.get("/api/research/{task_id}")
def get_research(task_id: str) -> dict:
    task = storage.load(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    data = task.summary_dict()
    data["report"] = task.report.model_dump() if task.report else None
    return data


@app.get("/api/research/{task_id}/markdown", response_class=PlainTextResponse)
def get_markdown(task_id: str) -> str:
    task = storage.load(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if not task.report:
        return f"# {task.topic}\n\n任务状态：{task.status}\n"
    return task.report.to_markdown()


if __name__ == "__main__":
    import uvicorn

    from backend.config import setup_logging

    setup_logging()
    print(f"启动服务：http://127.0.0.1:8000  （接口文档 /docs）")
    print("llm_ready:", bool(config.DEEPSEEK_API_KEY), "| search_ready:", bool(config.BOCHA_API_KEY))
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
