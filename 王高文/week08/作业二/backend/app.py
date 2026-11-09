"""FastAPI 服务：/health、POST /api/research、GET /api/research/{rid}、GET /api/research。

运行方式：POST 返回 202 + research_id，`asyncio.create_task` 后台执行，
前端轮询 GET /api/research/{id} 查看 pending/running/completed/failed 与中间结果。
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import storage
from .research import run_research

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # 服务端统一打开日志（uvicorn 自带 logger 不重复）
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("深度研究助手后端启动")
    yield


app = FastAPI(title="深度研究助手", version="0.1.0", lifespan=_lifespan)


class ResearchRequest(BaseModel):
    topic: str
    max_rounds: int | None = Field(default=None, ge=1, le=6)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/research", status_code=202)
async def create_research(req: ResearchRequest):
    topic = (req.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic 不能为空")
    rid = uuid.uuid4().hex
    logger.info("收到研究请求 rid=%s topic=%r max_rounds=%s", rid, topic[:50], req.max_rounds)
    asyncio.create_task(run_research(rid, topic, req.max_rounds))
    return {"research_id": rid, "status": "pending"}


@app.get("/api/research/{rid}")
async def get_research(rid: str):
    rec = storage.load(rid)
    if rec is None:
        raise HTTPException(status_code=404, detail="研究记录不存在")
    return rec.model_dump()


@app.get("/api/research")
async def list_research():
    return [r.model_dump() for r in storage.list_all()]


if __name__ == "__main__":
    import uvicorn

    routes = sorted({getattr(r, "path", "") for r in app.routes if getattr(r, "path", "").startswith("/")})
    print("=== 已注册路由 ===")
    for p in routes:
        print("  ", p)
    print("启动开发服务器 http://127.0.0.1:8000  (可用 `bash start.sh` 或 `python -m uvicorn backend.app:app --reload`)")
    uvicorn.run(app, host="127.0.0.1", port=8000)