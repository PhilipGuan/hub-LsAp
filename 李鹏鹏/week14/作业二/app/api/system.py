"""系统状态与配置接口：给前端状态条和运维排查用。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from .. import db, kafka_bus
from ..config import settings
from ..parsing import backend_status
from ..rag import llm as llm_module
from ..rag import refresh_index
from ..rag import stats as index_stats

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["system"])

VERSION = "1.0.0"


def _worker_status() -> dict[str, Any]:
    raw = db.get_runtime("worker_heartbeat")
    if not raw:
        return {"online": False, "mode": "unknown", "last_beat": None, "age_seconds": None}
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {"ts": raw}
    age: float | None = None
    stamp = str(payload.get("ts") or "")
    if stamp:
        try:
            age = (datetime.now() - datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")).total_seconds()
        except ValueError:
            age = None
    return {
        "online": age is not None and age < 30,
        "mode": payload.get("mode", "unknown"),
        "last_beat": stamp or None,
        "age_seconds": round(age, 1) if age is not None else None,
        "error": payload.get("error", ""),
    }


@router.get("/health")
def health():
    kafka_ok, kafka_message = kafka_bus.kafka_health()
    counts = db.document_counts()
    index = index_stats()
    parser = backend_status()
    worker = _worker_status()

    healthy = bool(index["chunks"] >= 0) and (kafka_ok or not settings.kafka_enabled)
    return {
        "ok": healthy,
        "version": VERSION,
        "kafka": {
            "enabled": settings.kafka_enabled,
            "ok": kafka_ok,
            "message": kafka_message,
            **kafka_bus.describe(),
        },
        "parser": parser,
        "worker": worker,
        "llm": llm_module.status(),
        "index": index,
        "documents": counts,
        "server_time": db.now(),
    }


@router.get("/config")
def config():
    data = settings.public_dict()
    data["parser_status"] = backend_status()
    data["index"] = index_stats()
    data["worker"] = _worker_status()
    data["version"] = VERSION
    return data


@router.post("/index/refresh")
def refresh(force: bool = True):
    refresh_index(force=force)
    return {"ok": True, "index": index_stats()}
