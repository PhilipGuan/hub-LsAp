"""作业1 · 存储层：数据访问仓库（Repository）。

对外只暴露/接收 core.entities 中的 pydantic 实体，
内部完成 SQLite 行 ⇄ 实体的转换。
"""
from __future__ import annotations

import json
import uuid
from contextlib import closing
from datetime import datetime

from core.config import MEMORY_WINDOW, TITLE_LENGTH
from core.entities import Memory, Message, Session, Sentiment
from storage.database import connect, init_db


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_dt(value: str | None) -> datetime:
    return datetime.fromisoformat(value) if value else datetime.now()


# ---------------------------------------------------------------- 会话


def create_session(title: str = "新会话") -> Session:
    session_id = uuid.uuid4().hex
    now = _now()
    with closing(connect()) as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        conn.commit()
    return Session(
        session_id=session_id, title=title,
        created_at=_to_dt(now), updated_at=_to_dt(now),
    )


def get_session(session_id: str) -> Session | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        return None
    return Session(
        session_id=row["session_id"], title=row["title"],
        created_at=_to_dt(row["created_at"]),
        updated_at=_to_dt(row["updated_at"]),
    )


def list_sessions() -> list[Session]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [
        Session(
            session_id=r["session_id"], title=r["title"],
            created_at=_to_dt(r["created_at"]),
            updated_at=_to_dt(r["updated_at"]),
        )
        for r in rows
    ]


def update_title(session_id: str, title: str) -> None:
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE sessions SET title = ? WHERE session_id = ?",
            (title[:TITLE_LENGTH], session_id),
        )
        conn.commit()


def touch_session(session_id: str) -> None:
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (_now(), session_id),
        )
        conn.commit()


def delete_session(session_id: str) -> bool:
    """删除会话及其全部消息与长期记忆。返回是否存在。"""
    with closing(connect()) as conn:
        found = conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if found is None:
            return False
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM memories WHERE session_id = ?", (session_id,))
        conn.commit()
    return True


# ---------------------------------------------------------------- 消息（短期记忆）


def add_message(
    session_id: str,
    role: str,
    content: str,
    sentiment: Sentiment | None = None,
) -> Message:
    now = _now()
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, sentiment, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, sentiment.value if sentiment else None, now),
        )
        conn.commit()
        msg_id = cur.lastrowid
    return Message(
        id=msg_id, session_id=session_id, role=role,  # type: ignore[arg-type]
        content=content, sentiment=sentiment, created_at=_to_dt(now),
    )


def count_messages(session_id: str) -> int:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["n"])


def _row_to_message(row) -> Message:
    sentiment = (
        Sentiment(row["sentiment"]) if row["sentiment"] in Sentiment._value2member_map_
        else None
    )
    return Message(
        id=row["id"], session_id=row["session_id"], role=row["role"],
        content=row["content"], sentiment=sentiment,
        created_at=_to_dt(row["created_at"]),
    )


def get_recent_messages(session_id: str, limit: int = MEMORY_WINDOW) -> list[Message]:
    """最近 N 条消息，按时间正序返回（先旧后新）。"""
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (session_id, limit),
        ).fetchall()
    return [_row_to_message(r) for r in rows]


def get_all_messages(session_id: str) -> list[Message]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [_row_to_message(r) for r in rows]


# ---------------------------------------------------------------- 长期记忆


def _row_to_memory(row) -> Memory:
    return Memory(
        session_id=row["session_id"], key=row["key"],
        value=json.loads(row["value"]), updated_at=_to_dt(row["updated_at"]),
    )


def get_all_memories(session_id: str) -> list[Memory]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM memories WHERE session_id = ?", (session_id,)
        ).fetchall()
    return [_row_to_memory(r) for r in rows]


def get_memory_value(session_id: str, key: str) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT value FROM memories WHERE session_id = ? AND key = ?",
            (session_id, key),
        ).fetchone()
    return json.loads(row["value"]) if row else None


def upsert_memory(session_id: str, key: str, value: dict) -> Memory:
    now = _now()
    with closing(connect()) as conn:
        conn.execute(
            "INSERT INTO memories (session_id, key, value, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (session_id, key) DO UPDATE SET "
            "value = excluded.value, updated_at = excluded.updated_at",
            (session_id, key, json.dumps(value, ensure_ascii=False), now),
        )
        conn.commit()
    return Memory(
        session_id=session_id, key=key, value=value,
        updated_at=_to_dt(now),
    )
