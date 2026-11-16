"""多模态问答接口。

一次请求的完整链路
------------------
问题（可带一张图片）
  → 图问时先用视觉模型生成图片描述，扩展检索查询
  → BM25/向量检索「正文 + 表格 + 图片块」
  → 拼上下文，把命中的图片/页面截图一起送给多模态模型
  → 模型输出 Markdown（含 IMG:n 图片标记）
  → 后端替换成真实图片地址并渲染 HTML
  → 落库（会话历史）+ 返回前端
"""
from __future__ import annotations

import base64
import binascii
import logging

from fastapi import APIRouter, HTTPException

from .. import db
from ..config import settings
from ..rag import answer_question, retrieve, search_debug
from ..rag import llm as llm_module
from ..rag.llm import LLMError, to_data_url
from .schemas import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

_MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _normalize_image(image: str | None) -> str | None:
    """校验并压缩前端传来的 data URL。"""
    if not image:
        return None
    if not image.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="图片格式不正确（需要 data:image/...）")
    _, _, payload = image.partition(",")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="图片 base64 解码失败") from exc
    if not raw:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(raw) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="图片不能超过 8 MB")
    try:
        return to_data_url(raw, max_side=1000)
    except Exception as exc:  # noqa: BLE001 - 无法识别的图片
        raise HTTPException(status_code=400, detail=f"图片无法解析：{exc}") from exc


@router.post("")
def chat(request: ChatRequest):
    question = (request.question or "").strip()
    if not question and not request.image:
        raise HTTPException(status_code=400, detail="请输入问题，或上传一张图片")
    if not settings.llm_ready:
        raise HTTPException(
            status_code=503,
            detail="尚未配置大模型：请在 .env 填写 LLM_API_KEY（或 QWEN_API_KEY），"
            "或设置 LLM_MOCK=1 使用离线演示模式。",
        )

    session_id = request.session_id or db.new_id("sess_")
    image = _normalize_image(request.image)
    history = db.list_messages(session_id, limit=8) if request.session_id else []

    effective_question = question or "请描述这张图片的内容，并结合文档资料说明它与文档的关系。"
    retrieval = retrieve(
        effective_question,
        doc_ids=request.doc_ids or None,
        question_image=image,
    )

    try:
        result = answer_question(
            effective_question,
            retrieval,
            question_image=image,
            history=history,
        )
    except LLMError as exc:
        logger.warning("模型调用失败: %s", exc.message)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    user_message = db.add_message(
        session_id,
        "user",
        content_md=question or "（图片提问）",
        images=[{"url": image, "caption": "用户上传的图片"}] if image else [],
    )
    assistant_message = db.add_message(
        session_id,
        "assistant",
        content_md=result["answer_md"],
        content_html=result["answer_html"],
        sources=result["sources"],
        images=result["images"],
        provider=result["provider"],
        model=result["model"],
        elapsed_ms=result["elapsed_ms"],
    )

    return {
        "session_id": session_id,
        "user_message": user_message,
        "message": assistant_message,
        "retrieval": result["retrieval"],
        "warnings": result.get("warnings", []),
        "mock": result["mock"],
        "llm": llm_module.status(),
    }


@router.get("/debug/retrieve")
def debug_retrieve(q: str, doc_id: str | None = None):
    """检索调试接口：看清到底召回了哪些块。"""
    return search_debug(q, [doc_id] if doc_id else None)


@router.get("/{session_id}")
def history(session_id: str, limit: int = 100):
    messages = db.list_messages(session_id, limit=max(1, min(limit, 300)))
    return {"session_id": session_id, "messages": messages}


@router.delete("/{session_id}")
def clear(session_id: str):
    db.clear_messages(session_id)
    return {"ok": True, "session_id": session_id}
