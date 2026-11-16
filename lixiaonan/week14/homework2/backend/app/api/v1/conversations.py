"""会话接口（功能3 / F3-7）：会话列表、历史消息。"""
import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ok
from app.services import chat_service

router = APIRouter()


@router.get("")
def list_conversations(db: Session = Depends(get_db)):
    convs = chat_service.list_conversations(db)
    return ok([
        {"id": c.id, "title": c.title, "created_at": c.created_at.isoformat()}
        for c in convs
    ])


@router.get("/{conversation_id}/messages")
def list_messages(conversation_id: str, db: Session = Depends(get_db)):
    messages = chat_service.list_messages(db, conversation_id)
    return ok([
        {
            "id": m.id,
            "conversation_id": m.conversation_id,
            "role": m.role,
            "text_content": m.text_content,
            "image_urls": (json.loads(m.image_urls) if m.image_urls else []),
            "citations": (json.loads(m.citations) if m.citations else []),
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ])
