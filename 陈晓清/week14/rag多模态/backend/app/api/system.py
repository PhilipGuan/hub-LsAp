# -*- coding: utf-8 -*-
"""健康检查与统计。"""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.config import DATA_DIR, MINERU_BIN
from ..core.db import get_db
from ..orm import File as FileRecord
from ..schemas import HealthItem, HealthResponse, StatsResponse
from ..services import kafka_bus, llm, vectorstore

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health():
    kafka_ok = kafka_bus.broker_available(timeout=1.5)
    milvus_ok, milvus_detail = vectorstore.check_connection()
    llm_ok, llm_detail = llm.check_llm()
    mineru_path = shutil.which(MINERU_BIN)

    return HealthResponse(
        api=HealthItem(ok=True, detail="运行中"),
        kafka=HealthItem(ok=kafka_ok, detail="可连接" if kafka_ok else "不可用，worker 将使用轮询"),
        milvus=HealthItem(ok=milvus_ok, detail=milvus_detail),
        llm=HealthItem(ok=llm_ok, detail=llm_detail),
        mineru=HealthItem(
            ok=bool(mineru_path),
            detail=mineru_path or "找不到 mineru 命令",
        ),
    )


@router.get("/stats", response_model=StatsResponse)
def stats(db: Session = Depends(get_db)):
    files = db.query(func.count(FileRecord.id)).scalar() or 0
    chunks = db.query(func.coalesce(func.sum(FileRecord.chunk_count), 0)).scalar() or 0
    usage = shutil.disk_usage(DATA_DIR)
    return StatsResponse(
        files=int(files),
        chunks=int(chunks),
        milvus_rows=vectorstore.count_rows(),
        disk_free_gb=round(usage.free / (1024**3), 2),
    )
