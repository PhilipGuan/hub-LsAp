# -*- coding: utf-8 -*-
"""图文问答接口。"""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..core.config import (
    CHAT_IMAGE_DIR,
    MAX_INLINE_IMAGES,
    TOP_K,
    ensure_dirs,
)
from ..schemas import ChatResponse, ChatSource, ChatTimings
from ..services import llm, vectorstore
from ..services.image_service import (
    extract_image_refs,
    image_url,
    normalize_answer_images,
    resolve_local_image,
)

router = APIRouter(prefix="/chat", tags=["chat"])
PAGE_PREFIX_RE = re.compile(r"^\[第(\d+)页\]\s*")


def _page_of(text: str) -> int:
    match = PAGE_PREFIX_RE.match(text or "")
    return int(match.group(1)) if match else 0


def _merge_hits(*hit_lists: list[dict], top_k: int) -> list[dict]:
    merged: dict[tuple, dict] = {}
    for hits in hit_lists:
        for hit in hits:
            key = (hit.get("file_id"), hit.get("text"))
            old = merged.get(key)
            if old is None or (hit.get("distance") or 0) < (old.get("distance") or 0):
                merged[key] = hit
    ordered = sorted(merged.values(), key=lambda item: item.get("distance") or 0)
    return ordered[:top_k]


@router.post("", response_model=ChatResponse)
async def chat(
    question: str = Form(...),
    image: UploadFile | None = File(default=None),
    top_k: int = Form(default=TOP_K),
):
    question = (question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    top_k = max(1, min(int(top_k), 20))

    ensure_dirs()
    user_image: Path | None = None
    if image is not None and image.filename:
        raw = await image.read()
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="图片不能超过 5MB")
        suffix = Path(image.filename).suffix.lower() or ".png"
        if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            raise HTTPException(status_code=415, detail="图片格式不支持")
        user_image = CHAT_IMAGE_DIR / f"{uuid.uuid4().hex}{suffix}"
        user_image.write_bytes(raw)

    retrieve_started = time.time()
    try:
        text_hits = vectorstore.search_text(question, max(8, top_k * 2))
    except Exception as exc:
        if user_image is not None:
            user_image.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=f"向量检索失败：{type(exc).__name__}: {exc}")

    image_hits: list[dict] = []
    if user_image is not None:
        try:
            image_hits = vectorstore.search_image(user_image, 4)
        except Exception:
            image_hits = []

    hits = _merge_hits(text_hits, image_hits, top_k=top_k)
    for hit in hits:
        hit["page"] = _page_of(hit.get("text") or "")
    retrieve_ms = int((time.time() - retrieve_started) * 1000)

    try:
        if not hits and user_image is None:
            return ChatResponse(
                answer="知识库中没有检索到相关内容。请先上传并解析文档，或换一种问法。",
                used_image=False,
                sources=[],
                timings=ChatTimings(retrieve_ms=retrieve_ms, llm_ms=0),
            )

        answer_text, llm_ms = llm.answer(question, hits, user_image=user_image)
        answer_text = normalize_answer_images(answer_text)

        sources: list[ChatSource] = []
        for hit in hits:
            images = []
            for ref in extract_image_refs(hit.get("text") or ""):
                real = resolve_local_image(ref)
                if real is not None:
                    images.append(image_url(real))
                if len(images) >= MAX_INLINE_IMAGES:
                    break
            text = PAGE_PREFIX_RE.sub("", hit.get("text") or "")
            sources.append(
                ChatSource(
                    file_id=hit.get("file_id"),
                    file_name=hit.get("file_name") or "",
                    page=hit.get("page") or 0,
                    score=float(hit["distance"]) if hit.get("distance") is not None else None,
                    snippet=text[:200].replace("\n", " "),
                    images=images,
                )
            )

        return ChatResponse(
            answer=answer_text,
            used_image=user_image is not None,
            sources=sources,
            timings=ChatTimings(retrieve_ms=retrieve_ms, llm_ms=llm_ms),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"生成回答失败：{type(exc).__name__}: {exc}")
    finally:
        if user_image is not None:
            user_image.unlink(missing_ok=True)
