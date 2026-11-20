"""问答服务（功能3 / F3-4~F3-8）：SSE 编排。

流程：会话管理 -> 用户图片转存 -> 检索（向量/关键词降级）-> 组装多模态 Prompt
   -> LLM 流式生成（openai 后端 / mock 桩）-> 逐段 SSE 推送 -> 持久化消息与来源。

SSE 事件（data: JSON\\n\\n）：
  meta   {conversation_id, title}      会话信息
  status {content}                     阶段提示（检索中 / 生成中）
  chunk  {content}                     回答文本增量
  done   {citations: [...]}            结束 + 来源引用
  error  {message}                     异常
"""
import asyncio
import base64
import json

from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BizError, ErrorCode
from app.db.models import ChunkType, Conversation, Document, Message
from app.schemas.chat import ChatRequest, CitationItem
from app.services.llm_client import LLMError
from app.services.retrieval_service import ContextItem, retrieve
from app.services.storage_service import BaseStorage
from app.utils.file_utils import new_id

SYSTEM_PROMPT = """你是多模态 RAG 问答助手，请严格遵守：
1. 仅基于【参考资料】回答用户问题；资料不足时如实说明，不要编造内容。
2. 回答使用 Markdown 格式，条理清晰，必要时使用列表与表格。
3. 引用资料时标注来源编号，如 [资料1]、[资料2]。
4. 需要展示图片时，使用 Markdown 图片语法 ![图片](URL)，且只能使用参考资料中给出的 URL，不得编造 URL。
5. 用户上传的图片也是问题的一部分，请结合图片内容作答。"""

_IMAGE_EXT_BY_MAGIC = [(b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpeg"), (b"RIFF", "webp")]
HISTORY_MAX_MESSAGES = 6


# ============ SSE 响应入口 ============
async def chat_sse_response(req: ChatRequest, db: Session, storage: BaseStorage) -> StreamingResponse:
    async def event_stream():
        try:
            settings = get_settings()

            conv = _get_or_create_conversation(db, req.conversation_id, req.query)
            yield _sse({"type": "meta", "conversation_id": conv.id, "title": conv.title})

            user_images = _prepare_user_images(req.images, storage, conv.id)

            yield _sse({"type": "status", "content": "正在检索知识库..."})
            top_k = req.top_k or settings.retrieval_top_k
            items = retrieve(db, req.query, settings, req.document_ids, top_k)
            logger.info(f"问答检索完成 conv={conv.id} 召回={len(items)} query={req.query[:50]!r}")
            yield _sse({"type": "status", "content": f"已召回 {len(items)} 个相关片段，正在生成回答..."})

            messages = _build_multimodal_messages(req.query, user_images, items, storage, db, conv.id)
            full_text = ""
            async for delta in _llm_stream(settings, req.query, items, messages):
                full_text += delta
                yield _sse({"type": "chunk", "content": delta})

            citations = [_citation(it) for it in items]
            _persist_messages(db, conv, req.query, user_images, full_text, citations)
            yield _sse({"type": "done", "citations": citations})
        except (BizError, LLMError) as exc:
            yield _sse({"type": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"问答处理异常: {exc}")
            yield _sse({"type": "error", "message": f"服务内部错误: {exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ============ 会话与消息（F3-7） ============
def _get_or_create_conversation(db: Session, conversation_id: str | None, query: str) -> Conversation:
    if conversation_id:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            raise BizError(ErrorCode.NOT_FOUND, f"会话不存在: {conversation_id}", 404)
        return conv
    conv = Conversation(id=new_id(), title=query[:30])
    db.add(conv)
    db.commit()
    return conv


def _load_history(db: Session, conversation_id: str, limit: int = HISTORY_MAX_MESSAGES) -> list[Message]:
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed([m for m in rows if m.text_content]))


def _persist_messages(db: Session, conv: Conversation, query: str, user_images,
                      answer: str, citations: list[dict]) -> None:
    db.add(Message(
        id=new_id(), conversation_id=conv.id, role="user",
        text_content=query,
        image_urls=json.dumps([u["storage_url"] for u in user_images], ensure_ascii=False),
    ))
    db.add(Message(
        id=new_id(), conversation_id=conv.id, role="assistant",
        text_content=answer, citations=json.dumps(citations, ensure_ascii=False),
    ))
    if conv.title in ("新对话", ""):
        conv.title = query[:30]
    db.commit()


def list_conversations(db: Session, limit: int = 50) -> list[Conversation]:
    return db.query(Conversation).order_by(Conversation.updated_at.desc()).limit(limit).all()


def list_messages(db: Session, conversation_id: str) -> list[Message]:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise BizError(ErrorCode.NOT_FOUND, f"会话不存在: {conversation_id}", 404)
    return db.query(Message).filter(Message.conversation_id == conversation_id)\
        .order_by(Message.created_at.asc()).all()


# ============ 用户图片（图问，F3-1） ============
def _image_ext(raw: bytes) -> str:
    for magic, ext in _IMAGE_EXT_BY_MAGIC:
        if raw.startswith(magic):
            return ext
    return "png"


def _prepare_user_images(images_b64: list[str], storage: BaseStorage, conv_id: str) -> list[dict]:
    """解码并转存用户图片，返回 [{storage_url, data_url}]。"""
    results = []
    for idx, b64 in enumerate(images_b64):
        raw = base64.b64decode(b64)
        ext = _image_ext(raw)
        key = f"chat/{conv_id}/{idx}.{ext}"
        storage.save(key, raw)
        results.append({
            "storage_url": storage.url_for(key),
            "data_url": f"data:image/{ext};base64,{b64}",
        })
    return results


# ============ 多模态 Prompt 组装（F3-4） ============
def _context_block(items: list[ContextItem]) -> str:
    lines = ["【参考资料】"]
    text_no = img_no = 0
    for it in items:
        page = it.page_no if it.page_no is not None else "?"
        if it.chunk_type == ChunkType.TEXT.value:
            text_no += 1
            lines.append(f"[资料{text_no}]（{it.file_name} 第{page}页）\n{it.content}")
        else:
            img_no += 1
            lines.append(f"[图片{img_no}]（{it.file_name} 第{page}页，URL: {it.image_url}）")
    if text_no == 0 and img_no == 0:
        lines.append("（知识库中未检索到相关资料，请如实告知用户）")
    return "\n\n".join(lines)


def _build_multimodal_messages(query: str, user_images: list[dict], items: list[ContextItem],
                               storage: BaseStorage, db: Session, conv_id: str) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in _load_history(db, conv_id):
        messages.append({"role": m.role, "content": m.text_content})

    user_text = _context_block(items) + f"\n\n【用户问题】{query}"
    if user_images:
        user_text += "\n\n（用户还上传了图片，请结合图片内容回答）"

    content: list[dict] = [{"type": "text", "text": user_text}]
    # 召回的资料图片 -> data URL（云端 LLM 无法访问本地 /static URL，必须传字节）
    for it in items:
        if it.chunk_type == ChunkType.IMAGE.value and it.image_key:
            raw = storage.read_bytes(it.image_key)
            b64 = base64.b64encode(raw).decode()
            ext = it.image_key.rsplit(".", 1)[-1]
            content.append({"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}})
    # 用户上传的图片（图问）
    for ui in user_images:
        content.append({"type": "image_url", "image_url": {"url": ui["data_url"]}})

    messages.append({"role": "user", "content": content})
    return messages


# ============ LLM 调用（F3-4 / F3-8） ============
async def _llm_stream(settings, query: str, items: list[ContextItem], messages: list[dict]):
    """openai 后端走真实流式；mock 后端基于检索结果生成可预期的桩回答。"""
    if settings.llm_backend == "mock":
        preset = _build_mock_answer(query, items)
        for i in range(0, len(preset), 24):
            await asyncio.sleep(0.03)
            yield preset[i:i + 24]
        return
    from app.services.llm_client import get_llm_client

    client = get_llm_client(settings)
    async for delta in client.stream_chat(messages):
        yield delta


def _build_mock_answer(query: str, items: list[ContextItem]) -> str:
    lines = [f"**（Mock 模式）** 已从知识库检索到 {len(items)} 个相关片段，围绕「{query}」整理如下：", ""]
    text_no = 0
    for it in items:
        if it.chunk_type == ChunkType.TEXT.value:
            text_no += 1
            snippet = it.content[:150].replace("\n", " ")
            page = it.page_no if it.page_no is not None else "?"
            lines.append(f"{text_no}. {snippet}……（来源：{it.file_name} 第{page}页）")
    image = next((c for c in items if c.chunk_type == ChunkType.IMAGE.value and c.image_url), None)
    if image:
        page = image.page_no if image.page_no is not None else "?"
        lines += ["", "**检索命中的资料图片：**", "", f"![资料图片]({image.image_url})", "",
                  f"（来源：{image.file_name} 第{page}页）"]
    lines += ["", "> 配置真实 LLM 后（`.env` 中 `LLM_BACKEND=openai` + `LLM_API_KEY`），"
              "回答将由多模态大模型生成，并支持结合用户上传的图片作答。"]
    return "\n".join(lines)


def _citation(it: ContextItem) -> dict:
    return CitationItem(
        chunk_type=it.chunk_type, document_id=it.document_id, file_name=it.file_name,
        page_no=it.page_no, image_url=it.image_url, score=it.score,
    ).model_dump(mode="json")
