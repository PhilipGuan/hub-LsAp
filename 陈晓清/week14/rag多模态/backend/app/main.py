# -*- coding: utf-8 -*-
"""FastAPI 入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import chat, files, images, system
from .core.config import ensure_dirs
from .core.db import init_db

logger = logging.getLogger("rag.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    init_db()
    logger.info("后端启动完成")
    yield


app = FastAPI(
    title="多模态 RAG 问答后端",
    version="1.0.0",
    description="文件上传、MinerU 解析、向量检索与图文问答",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(files.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(images.router, prefix="/api")
app.include_router(system.router, prefix="/api")


@app.get("/", tags=["system"])
def root():
    return {
        "name": "多模态 RAG 问答后端",
        "docs": "/docs",
        "health": "/api/health",
    }
