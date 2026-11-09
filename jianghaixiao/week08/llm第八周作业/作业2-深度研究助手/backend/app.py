"""FastAPI 服务入口。"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from . import config, research, storage
from .models import ResearchCreated, ResearchRecord, ResearchRequest


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_data_dir()
    yield


app = FastAPI(
    title="深度研究助手",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mock_mode": config.mock_mode()}


@app.post("/api/research", response_model=ResearchCreated, status_code=202)
async def create_research(request: ResearchRequest) -> ResearchCreated:
    topic = request.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic 不能为空")
    research_id = uuid.uuid4().hex
    storage.create(research_id, topic)
    asyncio.create_task(research.run_research(research_id, topic))
    return ResearchCreated(research_id=research_id)


@app.get("/api/research", response_model=list[ResearchRecord])
async def list_research() -> list[ResearchRecord]:
    return storage.list_all()


@app.get("/api/research/{research_id}", response_model=ResearchRecord)
async def get_research(research_id: str) -> ResearchRecord:
    record = storage.get(research_id)
    if record is None:
        raise HTTPException(status_code=404, detail="研究记录不存在")
    return record


@app.get("/api/research/{research_id}/html", response_class=HTMLResponse)
async def get_report_html(research_id: str) -> HTMLResponse:
    record = storage.get(research_id)
    if record is None:
        raise HTTPException(status_code=404, detail="研究记录不存在")
    if not record.report_html:
        raise HTTPException(status_code=409, detail="报告尚未生成")
    return HTMLResponse(content=record.report_html)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.app:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
    )
