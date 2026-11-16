"""FastAPI application: upload, document status, SSE events, and multimodal chat."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    APP_TITLE,
    BASE_DIR,
    CHAT_IMAGE_MAX_MB,
    DOCUMENT_MAX_MB,
    INGEST_TOPIC,
    KAFKA_BOOTSTRAP_SERVERS,
    LLM_MODEL,
    MINERU_BIN,
    MINERU_TIER,
    PARSED_DIR,
    RETRIEVAL_IMAGE_TOP_K,
    RETRIEVAL_TEXT_TOP_K,
    STATUS_TOPIC,
    UPLOAD_DIR,
)
from .db import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    create_document,
    delete_document,
    document_detail,
    get_document,
    init_db,
    list_documents,
    now_iso,
    stats,
    update_document,
)
from .kafka_bus import KafkaPublisher, StatusEventBroker, ensure_topics
from .llm import LLMError, llm_health
from .qa import answer
from .schemas import ChatRequest

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

ALLOWED_TIERS = {"flash", "basic", "standard", "advanced"}
STATIC_DIR = BASE_DIR / "app" / "static"
MAX_UPLOAD_FILES = 20
PDF_MAGIC = b"%PDF-"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    publisher = KafkaPublisher()
    try:
        await ensure_topics()
    except Exception as exc:
        logger.error("could not ensure Kafka topics: %s", exc)
    await publisher.start()

    broker = StatusEventBroker()
    await broker.start()

    app.state.publisher = publisher
    app.state.broker = broker
    logger.info("Mini multimodal RAG ready at http://localhost:8080")
    try:
        yield
    finally:
        await broker.stop()
        await publisher.stop()


app = FastAPI(title=APP_TITLE, version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/media", StaticFiles(directory=PARSED_DIR), name="media")


def _safe_filename(name: str | None) -> str:
    base = Path((name or "document.pdf").replace("\\", "/")).name
    base = base.replace("\x00", "").strip()
    return base[:240] or "document.pdf"


def _validate_image_payload(data_url: str | None) -> None:
    if not data_url:
        return
    if not data_url.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="不支持的图片格式")
    _, _, payload = data_url.partition(",")
    if not payload:
        raise HTTPException(status_code=400, detail="图片数据为空")
    estimated = len(payload) * 3 // 4
    if estimated > CHAT_IMAGE_MAX_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"图片不能超过 {CHAT_IMAGE_MAX_MB} MB",
        )
    try:
        base64.b64decode(payload[:4096] + "===", validate=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="图片 base64 数据无效") from exc


async def _save_pdf(upload: UploadFile, document_id: str) -> tuple[Path, int, str]:
    filename = _safe_filename(upload.filename)
    if not filename.lower().endswith(".pdf"):
        raise ValueError("只支持 .pdf 文件")
    destination = UPLOAD_DIR / f"{document_id}.pdf"
    digest = hashlib.sha256()
    size = 0
    header = b""
    try:
        with destination.open("wb") as output:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                if not header:
                    header = chunk[:8]
                size += len(chunk)
                if size > DOCUMENT_MAX_MB * 1024 * 1024:
                    raise ValueError(f"文件超过 {DOCUMENT_MAX_MB} MB 限制")
                digest.update(chunk)
                output.write(chunk)
        if size == 0:
            raise ValueError("文件为空")
        if not header.startswith(PDF_MAGIC):
            raise ValueError("文件内容不是合法的 PDF")
        return destination, size, digest.hexdigest()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    publisher: KafkaPublisher = app.state.publisher
    try:
        llm_ok, llm_detail = await asyncio.wait_for(llm_health(), timeout=3)
    except Exception as exc:
        llm_ok, llm_detail = False, str(exc)
    mineru_ready = bool(shutil.which(MINERU_BIN) or Path(MINERU_BIN).is_file())
    return {
        "ok": publisher.ready,
        "kafka": {
            "ready": publisher.ready,
            "bootstrap": KAFKA_BOOTSTRAP_SERVERS,
            "ingest_topic": INGEST_TOPIC,
            "status_topic": STATUS_TOPIC,
            "error": publisher.error,
        },
        "llm": {"ready": llm_ok, "model": LLM_MODEL, "detail": llm_detail},
        "mineru": {"ready": mineru_ready, "binary": MINERU_BIN, "default_tier": MINERU_TIER},
        "server_time": now_iso(),
    }


@app.get("/api/config")
async def config() -> dict[str, Any]:
    return {
        "tiers": sorted(ALLOWED_TIERS),
        "default_tier": MINERU_TIER,
        "llm_model": LLM_MODEL,
        "max_document_mb": DOCUMENT_MAX_MB,
        "max_chat_image_mb": CHAT_IMAGE_MAX_MB,
        "retrieval": {
            "text_top_k": RETRIEVAL_TEXT_TOP_K,
            "image_top_k": RETRIEVAL_IMAGE_TOP_K,
        },
    }


@app.post("/api/upload", status_code=202)
async def upload_documents(
    files: list[UploadFile] = File(...),
    tier: str = Form(default=MINERU_TIER),
) -> JSONResponse:
    publisher: KafkaPublisher = app.state.publisher
    if not publisher.ready:
        raise HTTPException(
            status_code=503,
            detail="Kafka 尚未连接，无法提交解析任务。请检查 Kafka 服务。",
        )
    if not files:
        raise HTTPException(status_code=400, detail="没有收到文件")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400, detail=f"单次最多上传 {MAX_UPLOAD_FILES} 个 PDF"
        )
    requested_tier = tier if tier in ALLOWED_TIERS else MINERU_TIER

    created: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for upload in files:
        document_id = uuid.uuid4().hex
        display_name = _safe_filename(upload.filename)
        try:
            stored_path, size, sha256 = await _save_pdf(upload, document_id)
        except Exception as exc:
            rejected.append({"filename": display_name, "error": str(exc)})
            continue
        try:
            document = create_document(
                document_id=document_id,
                filename=display_name,
                stored_path=str(stored_path),
                size=size,
                sha256=sha256,
                tier=requested_tier,
            )
            await publisher.enqueue_document(document)
            await publisher.publish_document(document)
            created.append(document)
        except Exception as exc:
            logger.exception("failed to enqueue %s", display_name)
            stored_path.unlink(missing_ok=True)
            update_document(
                document_id,
                status=STATUS_FAILED,
                stage="任务提交失败",
                error=str(exc)[:1200],
            )
            rejected.append({"filename": display_name, "error": f"提交 Kafka 失败：{exc}"})

    if not created:
        raise HTTPException(
            status_code=400,
            detail={"message": "没有 PDF 被接受", "rejected": rejected},
        )
    return JSONResponse(
        status_code=202,
        content={
            "documents": created,
            "rejected": rejected,
            "accepted": len(created),
        },
    )


@app.get("/api/documents")
async def documents() -> dict[str, Any]:
    return {"documents": list_documents(), "stats": stats()}


@app.get("/api/documents/{document_id}")
async def document(document_id: str) -> dict[str, Any]:
    detail = document_detail(document_id)
    if not detail:
        raise HTTPException(status_code=404, detail="文档不存在")
    excerpt = detail.get("excerpt") or ""
    excerpt = re.sub(
        r"\]\((images/[^)]+)\)", f"](/media/{document_id}/\\1)", excerpt
    )
    detail["excerpt"] = excerpt
    for image in detail["images"]:
        if image.get("image_path"):
            image["image_url"] = f"/media/{image['image_path']}"
    return detail


@app.delete("/api/documents/{document_id}")
async def remove_document(document_id: str) -> dict[str, Any]:
    document = get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    deleted = delete_document(document_id)
    if deleted:
        Path(deleted["stored_path"]).unlink(missing_ok=True)
        shutil.rmtree(PARSED_DIR / document_id, ignore_errors=True)
    return {"deleted": document_id}


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    broker: StatusEventBroker = app.state.broker

    async def stream() -> AsyncIterator[str]:
        yield "retry: 2500\n\n"
        try:
            async with broker.subscribe() as queue:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/chat")
async def chat(payload: ChatRequest) -> dict[str, Any]:
    _validate_image_payload(payload.image_data_url)
    doc_ids = payload.doc_ids or None
    if doc_ids:
        unknown = []
        not_ready = []
        for document_id in doc_ids:
            document = get_document(document_id)
            if not document:
                unknown.append(document_id)
            elif document["status"] != STATUS_COMPLETED:
                not_ready.append(document["filename"])
        if unknown:
            raise HTTPException(status_code=400, detail="部分文档不存在，请刷新列表")
        if not_ready:
            raise HTTPException(
                status_code=409,
                detail=f"以下文档还在解析中：{', '.join(not_ready)}",
            )
    try:
        result = await answer(
            question=payload.question,
            image_data_url=payload.image_data_url,
            doc_ids=doc_ids,
            history=[turn.model_dump() for turn in payload.history],
        )
        return result
    except LLMError as exc:
        logger.warning("chat LLM error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=f"问答失败：{exc}") from exc
