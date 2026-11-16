"""FastAPI 应用入口。

前后端不分离：前端文件放在 ``static/``，通过 ``/static`` 挂载；
``/media`` 挂载 ``data/documents``，用于直接访问解析出的图片、页面截图、
原始 PDF 和 Markdown。``python run.py`` 用 uvicorn 启动本应用。
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db, kafka_bus
from .api import chat as chat_api
from .api import documents as documents_api
from .api import system as system_api
from .config import settings
from .logging_setup import setup_logging
from .rag import refresh_index
from .worker import ParserWorker

logger = logging.getLogger(__name__)


def _warm_up() -> None:
    """后台预热：重建检索索引 + 探测/创建 Kafka topic。"""
    try:
        refresh_index(force=True)
        logger.info("检索索引预热完成：%s", refresh_index.__module__)
    except Exception:  # noqa: BLE001 - 预热失败不影响服务启动
        logger.exception("索引预热失败")
    try:
        ok, message = kafka_bus.ensure_topics()
        logger.info("Kafka %s：%s", "就绪" if ok else "不可用", message)
    except Exception:  # noqa: BLE001
        logger.exception("Kafka 初始化异常")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    db.init_db()
    (settings.data_dir / "documents").mkdir(parents=True, exist_ok=True)

    threading.Thread(target=_warm_up, name="rag-warm-up", daemon=True).start()

    worker = ParserWorker()
    app.state.worker = worker
    if settings.embed_worker:
        worker.start()
        logger.info("已启动内置解析工作进程（线程模式）")
    else:
        logger.info("EMBED_WORKER=0，不启动内置 worker；请另开进程执行 python -m app.worker")

    logger.info(
        "服务就绪 | Kafka=%s(%s) | 解析=%s | 模型=%s | 检索=%s",
        settings.kafka_bootstrap,
        "on" if settings.kafka_enabled else "off",
        settings.parser_backend,
        settings.llm_model or "未配置",
        settings.retrieval_mode,
    )
    try:
        yield
    finally:
        worker.stop()
        kafka_bus.close_producer()
        logger.info("服务已关闭")


def create_app() -> FastAPI:
    app = FastAPI(
        title="多模态 RAG 文档问答",
        description="Kafka + MinerU + 多模态大模型的 PDF 图文问答演示",
        version=system_api.VERSION,
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=800)

    app.include_router(documents_api.router)
    app.include_router(chat_api.router)
    app.include_router(system_api.router)

    static_dir = settings.static_dir
    index_page = static_dir / "index.html"
    if not index_page.is_file():
        logger.warning("前端入口不存在：%s", index_page)
    app.mount("/static", StaticFiles(directory=static_dir, check_dir=False), name="static")
    settings.documents_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.documents_dir), name="media")

    @app.get("/", include_in_schema=False)
    def index():
        if not index_page.is_file():
            return JSONResponse(
                status_code=500,
                content={"detail": f"前端文件缺失：{index_page}，请确认 static/ 目录完整"},
            )
        return FileResponse(index_page)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        icon = static_dir / "favicon.svg"
        if icon.is_file():
            return FileResponse(icon)
        return Response(status_code=204)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # pragma: no cover - 兜底
        logger.exception("未处理异常：%s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"服务器内部错误：{exc}"})

    return app


app = create_app()
