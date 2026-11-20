from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Message, Session as ChatSession, utcnow
from ..schemas import MessageOut, SessionCreate, SessionOut

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_db)):
    stmt = select(ChatSession).order_by(ChatSession.updated_at.desc())
    return db.scalars(stmt).all()


@router.post("", response_model=SessionOut)
def create_session(payload: SessionCreate | None = None, db: Session = Depends(get_db)):
    title = (payload.title if payload and payload.title else None) or "新会话"
    session = ChatSession(title=title[:120])
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.delete("/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)):
    session = db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.delete(session)
    db.commit()
    return {"ok": True}


@router.get("/{session_id}/messages", response_model=list[MessageOut])
def list_messages(session_id: str, db: Session = Depends(get_db)):
    session = db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
    )
    return db.scalars(stmt).all()


@router.patch("/{session_id}", response_model=SessionOut)
def rename_session(session_id: str, payload: SessionCreate, db: Session = Depends(get_db)):
    session = db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if payload.title:
        session.title = payload.title[:120]
        session.updated_at = utcnow()
        db.commit()
        db.refresh(session)
    return session
