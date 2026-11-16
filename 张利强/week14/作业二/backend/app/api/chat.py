import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import Message, Session as ChatSession, utcnow
from ..services.llm import image_to_b64, retrieve, sanitize_images, stream_answer

router = APIRouter(prefix="/api", tags=["chat"])
settings = get_settings()
log = logging.getLogger(__name__)


def _sse(event: str, payload: dict) -> dict:
    return {"event": event, "data": json.dumps(payload, ensure_ascii=False)}


@router.post("/chat")
async def chat(
    text: str = Form(""),
    session_id: str | None = Form(None),
    images: list[UploadFile] = File(default=[]),
):
    text = (text or "").strip()
    images = [i for i in images if i and i.filename]
    if not text and not images:
        raise HTTPException(status_code=400, detail="请输入问题或上传图片")

    chat_dir = settings.chat_dir
    chat_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    for upload in images:
        suffix = Path(upload.filename or "img.png").suffix.lower() or ".png"
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            raise HTTPException(status_code=400, detail=f"不支持的图片格式: {suffix}")
        path = chat_dir / f"{uuid.uuid4().hex}{suffix}"
        path.write_bytes(await upload.read())
        saved_paths.append(path)

    image_urls = [f"/static/chat/{p.name}" for p in saved_paths]
    image_b64 = [image_to_b64(p) for p in saved_paths]

    async def gen():
        db = SessionLocal()
        try:
            session = db.get(ChatSession, session_id) if session_id else None
            if session is None:
                session = ChatSession(title=(text[:30] or "图片问答"))
                db.add(session)
                db.flush()

            user_msg = Message(
                session_id=session.id,
                role="user",
                content=text,
                images_json=json.dumps(image_urls, ensure_ascii=False),
            )
            db.add(user_msg)
            session.updated_at = utcnow()
            db.commit()

            history = [
                {"role": m.role, "content": m.content}
                for m in db.scalars(
                    select(Message)
                    .where(Message.session_id == session.id, Message.id != user_msg.id)
                    .order_by(Message.created_at)
                ).all()
            ]

            yield _sse("session", {"session_id": session.id, "user_message_id": user_msg.id})

            sources = retrieve(text)
            yield _sse(
                "sources",
                {
                    "sources": [
                        {
                            "doc_id": s["doc_id"],
                            "filename": s["filename"],
                            "page": s["page"],
                            "heading": s.get("heading", ""),
                            "content": s.get("content", "")[:400],
                            "images": s.get("images", [])[:3],
                            "score": round(float(s.get("score") or 0), 6),
                        }
                        for s in sources
                    ]
                },
            )

            answer_parts: list[str] = []
            for token in stream_answer(text, sources, image_b64, history):
                answer_parts.append(token)
                yield _sse("token", {"t": token})

            answer = sanitize_images("".join(answer_parts))
            assistant = Message(
                session_id=session.id,
                role="assistant",
                content=answer,
                images_json="[]",
                sources_json=json.dumps(
                    [
                        {"filename": s["filename"], "page": s["page"], "doc_id": s["doc_id"]}
                        for s in sources
                    ],
                    ensure_ascii=False,
                ),
            )
            db.add(assistant)
            session.updated_at = utcnow()
            db.commit()
            db.refresh(assistant)

            yield _sse(
                "done",
                {
                    "message_id": assistant.id,
                    "content": answer,
                    "sources": [
                        {"filename": s["filename"], "page": s["page"], "doc_id": s["doc_id"]}
                        for s in sources
                    ],
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("chat failed")
            yield _sse("error", {"message": str(exc)})
        finally:
            db.close()

    return EventSourceResponse(gen(), headers={"X-Accel-Buffering": "no"})
