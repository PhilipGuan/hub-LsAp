"""极简 HTTP 层：POST 发起研究、GET 查询结果、静态 serve output/。

启动：python -m backend.web.server
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .. import config
from ..models import make_slug
from ..research import orchestrator

logger = logging.getLogger(__name__)

app = FastAPI(title="深度研究助手")

# 内存任务表（demo 级，进程重启即丢失）
_tasks: dict[str, dict] = {}

# 静态 serve 产物目录：/output/<slug>/report.html
config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(config.OUTPUT_DIR)), name="output")


@app.post("/research")
async def start_research(payload: dict) -> dict:
    topic = str((payload or {}).get("topic", "")).strip()
    if not topic:
        raise HTTPException(status_code=400, detail="缺少 topic 字段")
    record_id = make_slug(topic)
    _tasks[record_id] = {
        "status": "running",
        "topic": topic,
        "record": None,
        "error": None,
    }
    asyncio.create_task(_run(record_id, topic))
    return {"id": record_id, "status": "running", "topic": topic}


async def _run(record_id: str, topic: str) -> None:
    try:
        record = await orchestrator.research(topic, record_id=record_id)
        _tasks[record_id]["record"] = record
        _tasks[record_id]["status"] = "done"
    except Exception as exc:
        logger.exception("研究失败：%s", exc)
        _tasks[record_id]["status"] = "error"
        _tasks[record_id]["error"] = str(exc)


@app.get("/research")
async def list_research() -> dict:
    return {
        "tasks": [
            {"id": k, "status": v["status"], "topic": v["topic"]}
            for k, v in _tasks.items()
        ]
    }


@app.get("/research/{record_id}")
async def get_research(record_id: str) -> dict:
    task = _tasks.get(record_id)
    if task is None:
        raise HTTPException(status_code=404, detail="研究不存在")
    result = {"id": record_id, "status": task["status"], "topic": task["topic"]}
    if task["status"] == "done":
        result["report"] = task["record"].model_dump()
    elif task["status"] == "error":
        result["error"] = task["error"]
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.web.server:app", host="127.0.0.1", port=8000, reload=True)
