"""文档上传、列表、状态与预览接口。

上传流程（与 Kafka 解耦的关键点）
--------------------------------
1. 文件落盘到 ``data/documents/<doc_id>/original.pdf``；
2. 写入 SQLite，状态 ``pending``（前端显示「待解析」）；
3. 向 Kafka 主题 ``rag.doc.parse`` 投递解析任务（key = doc_id）；
4. 立即返回，解析由工作进程异步完成，前端轮询状态即可。

因此上传接口的响应时间与 PDF 大小、解析耗时无关。
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from .. import db, kafka_bus
from ..config import settings
from ..parsing import backend_status
from ..rag import refresh_index, render_markdown

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

_CHUNK = 1024 * 1024


def human_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def doc_public(doc: dict[str, Any]) -> dict[str, Any]:
    doc_id = str(doc["id"])
    status = str(doc["status"])
    return {
        "id": doc_id,
        "filename": doc["filename"],
        "size": int(doc["size"] or 0),
        "size_human": human_size(int(doc["size"] or 0)),
        "page_count": int(doc["page_count"] or 0),
        "status": status,
        "status_label": db.STATUS_LABELS.get(status, status),
        "progress": round(float(doc["progress"] or 0), 4),
        "stage": doc["stage"] or "",
        "parser": doc["parser"] or "",
        "chunk_count": int(doc["chunk_count"] or 0),
        "figure_count": int(doc["figure_count"] or 0),
        "summary": doc["summary"] or "",
        "error": doc["error"] or "",
        "source": doc["source"] or "",
        "created_at": doc["created_at"],
        "updated_at": doc["updated_at"],
        "started_at": doc["started_at"],
        "finished_at": doc["finished_at"],
        "pdf_url": f"/media/{doc_id}/original.pdf",
        "markdown_url": f"/media/{doc_id}/markdown.md",
    }


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in settings.allowed_ext else ""


async def _save_upload(upload: UploadFile, target: Path) -> tuple[int, str]:
    """流式落盘并计算 sha256，超限抛 413。"""
    limit = settings.max_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("wb") as handle:
            while True:
                chunk = await upload.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {settings.max_upload_mb} MB 上限",
                    )
                digest.update(chunk)
                handle.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"保存文件失败：{exc}") from exc
    return size, digest.hexdigest()


@router.post("")
async def upload_documents(files: list[UploadFile] = File(..., description="一个或多个 PDF")):
    """上传一个/多个 PDF，投递解析任务后立即返回。"""
    if not files:
        raise HTTPException(status_code=400, detail="没有收到文件")

    items: list[dict[str, Any]] = []
    warnings: list[str] = []

    for upload in files:
        filename = upload.filename or "unnamed.pdf"
        suffix = _safe_suffix(filename)
        if not suffix:
            warnings.append(f"{filename}：只支持 {', '.join(settings.allowed_ext)} 文件")
            await upload.close()
            continue

        doc_id = db.new_id("doc_")
        doc_dir = settings.ensure_doc_dir(doc_id)
        target = doc_dir / f"original{suffix}"
        try:
            size, sha256 = await _save_upload(upload, target)
        finally:
            await upload.close()

        if size == 0:
            shutil.rmtree(doc_dir, ignore_errors=True)
            warnings.append(f"{filename}：空文件，已忽略")
            continue

        existing = db.find_document_by_hash(sha256)
        if existing is not None:
            shutil.rmtree(doc_dir, ignore_errors=True)
            warnings.append(f"{filename}：与已存在的《{existing['filename']}》内容相同，已跳过")
            items.append({**doc_public(existing), "duplicated": True})
            continue

        doc = db.create_document(
            doc_id=doc_id,
            filename=filename,
            stored_path=str(target),
            size=size,
            sha256=sha256,
            source="kafka" if settings.kafka_enabled else "direct",
        )

        try:
            channel = kafka_bus.publish_parse_request(doc)
            if channel != doc["source"]:
                db.update_document(doc_id, source=channel)
                doc["source"] = channel
            kafka_bus.publish_status(doc_id, db.STATUS_PENDING, progress=0.0, stage="等待解析")
            db.add_event(doc_id, "upload", db.STATUS_PENDING, f"通过 {channel} 投递解析任务")
        except Exception as exc:  # noqa: BLE001 - 投递失败要让用户看到原因
            logger.warning("投递解析任务失败 doc=%s: %s", doc_id, exc)
            message = f"投递解析任务失败：{exc}"
            db.update_document(doc_id, status=db.STATUS_FAILED, stage="投递失败", error=message)
            db.add_event(doc_id, "upload", db.STATUS_FAILED, message)
            warnings.append(f"{filename}：{message}")
            doc = db.get_document(doc_id) or doc

        items.append(doc_public(doc))

    return {
        "items": items,
        "warnings": warnings,
        "kafka": kafka_bus.describe(),
        "parser": backend_status(),
    }


@router.get("")
def list_documents(limit: int = 100):
    docs = [doc_public(doc) for doc in db.list_documents(limit=max(1, min(limit, 500)))]
    return {
        "items": docs,
        "counts": db.document_counts(),
        "kafka": kafka_bus.describe(),
    }


@router.get("/{doc_id}")
def get_document(doc_id: str):
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {
        "item": doc_public(doc),
        "events": db.list_events(doc_id, limit=30),
    }


@router.get("/{doc_id}/preview")
def preview_document(doc_id: str, limit: int = 60000):
    """返回解析后的 Markdown 与渲染好的 HTML，供前端预览解析质量。"""
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    markdown_path = settings.doc_dir(doc_id) / "markdown.md"
    if not markdown_path.is_file():
        raise HTTPException(status_code=404, detail="还没有解析结果")
    text = markdown_path.read_text(encoding="utf-8", errors="replace")[: max(1000, limit)]
    # Markdown 里的图片是相对路径（images/xxx.png），预览时改写成 /media 地址
    text = text.replace("](images/", f"](/media/{doc_id}/images/")
    return {"markdown": text, "html": render_markdown(text), "item": doc_public(doc)}


@router.get("/{doc_id}/markdown", response_class=PlainTextResponse)
def download_markdown(doc_id: str):
    markdown_path = settings.doc_dir(doc_id) / "markdown.md"
    if not markdown_path.is_file():
        raise HTTPException(status_code=404, detail="还没有解析结果")
    return PlainTextResponse(
        markdown_path.read_text(encoding="utf-8", errors="replace"), media_type="text/markdown"
    )


@router.get("/{doc_id}/figures")
def list_figures(doc_id: str):
    """列出文档中的图片块，便于前端做「资料图库」。"""
    chunks = [chunk for chunk in db.fetch_chunks([doc_id]) if chunk["kind"] == "figure"]
    return {
        "items": [
            {
                "chunk_id": chunk["id"],
                "page": chunk["page"],
                "caption": chunk["caption"],
                "image_url": chunk["image_url"],
                "width": chunk["width"],
                "height": chunk["height"],
            }
            for chunk in chunks
        ]
    }


@router.post("/{doc_id}/reparse")
def reparse_document(doc_id: str):
    """重新解析：把状态重置为「待解析」并重新投递任务。"""
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc["status"] == db.STATUS_PARSING:
        raise HTTPException(status_code=409, detail="文档正在解析中，请稍候")

    db.update_document(
        doc_id,
        status=db.STATUS_PENDING,
        progress=0.0,
        stage="等待解析",
        error="",
        started_at=None,
        finished_at=None,
    )
    try:
        channel = kafka_bus.publish_parse_request(db.get_document(doc_id) or doc)
        db.update_document(doc_id, source=channel)
        kafka_bus.publish_status(doc_id, db.STATUS_PENDING, progress=0.0, stage="等待解析")
        db.add_event(doc_id, "reparse", db.STATUS_PENDING, f"重新投递（{channel}）")
    except Exception as exc:  # noqa: BLE001
        message = f"重新投递失败：{exc}"
        db.update_document(doc_id, status=db.STATUS_FAILED, stage="投递失败", error=message)
        db.add_event(doc_id, "reparse", db.STATUS_FAILED, message)
        raise HTTPException(status_code=502, detail=message) from exc
    return {"item": doc_public(db.get_document(doc_id) or doc)}


@router.delete("/{doc_id}")
def delete_document(doc_id: str):
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    db.delete_document(doc_id)
    shutil.rmtree(settings.doc_dir(doc_id), ignore_errors=True)
    refresh_index(force=True)
    return {"ok": True, "deleted": doc_id}
