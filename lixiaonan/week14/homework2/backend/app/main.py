"""FastAPI 应用入口。

启动：cd backend && uvicorn app.main:app --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.core.config import get_settings
from app.core.exceptions import install_exception_handlers
from app.core.logging import logger, setup_logging
from app.db.base import init_db
from app.mq.kafka_producer import MQProducer
from app.services.storage_service import build_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level, settings.logs_path)
    settings.ensure_dirs()
    init_db()
    app.state.storage = build_storage(settings)
    app.state.mq = MQProducer(settings)  # 懒连接；发送失败会按 F1-5 置 FAILED
    kafka_ready = app.state.mq.ping()
    logger.info(
        f"API 启动完成 port={settings.api_port} storage={settings.storage_backend} "
        f"parser={settings.parser_backend} kafka_ready={kafka_ready}"
    )
    yield
    if isinstance(app.state.mq, MQProducer):
        app.state.mq.close()
    logger.info("API 已关闭")


settings = get_settings()
settings.ensure_dirs()

app = FastAPI(title="多模态 RAG 问答系统", version="0.1.0", lifespan=lifespan)

install_exception_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api/v1")
# 本地存储模式下，解析图片 / 原件通过 /static 暴露（MinIO 模式下 URL 直连 MinIO）
app.mount(settings.static_url_prefix, StaticFiles(directory=str(settings.data_path)), name="static")


@app.get("/api/health")
def health():
    """健康检查：含 Kafka 连接状态。"""
    mq = getattr(app.state, "mq", None)
    return {
        "status": "ok",
        "kafka_ready": bool(mq and mq.ping()),
        "parser": settings.parser_backend,
        "storage": settings.storage_backend,
    }
