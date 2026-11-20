import asyncio
import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from ..events import bus

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events")
async def events():
    async def gen():
        queue = bus.subscribe()
        try:
            yield {"event": "ready", "data": "{}"}
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": "doc_status", "data": data}
        finally:
            bus.unsubscribe(queue)

    return EventSourceResponse(gen(), headers={"X-Accel-Buffering": "no"})


@router.get("/health")
def health():
    from ..config import get_settings
    from ..services.es_store import health as es_health

    settings = get_settings()
    return {
        "ok": True,
        "es": es_health(),
        "mock_llm": settings.use_mock_llm,
        "mock_embedding": settings.use_mock_embedding,
        "llm_model": settings.llm_model,
    }


@router.get("/stats")
def stats():
    from sqlalchemy import func, select

    from ..db import SessionLocal
    from ..models import Document

    db = SessionLocal()
    try:
        rows = db.execute(
            select(Document.status, func.count()).group_by(Document.status)
        ).all()
        return {status: count for status, count in rows}
    finally:
        db.close()
