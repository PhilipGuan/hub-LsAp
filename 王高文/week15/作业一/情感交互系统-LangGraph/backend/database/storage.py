"""使用 SQLite 按会话保存和读取聊天消息。"""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from backend.models.schemas import HistoryMessage


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATABASE_PATH = "data/chat.db"


class ChatStorage:
    """使用文件数据库持久化聊天历史。"""

    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = (
            path if path is not None else os.getenv("DATABASE_PATH") or _DEFAULT_DATABASE_PATH
        )
        self._path = _PROJECT_ROOT / Path(configured_path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)

        with closing(sqlite3.connect(self._path)) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chat_messages_by_session
                    ON chat_messages (session_id, id);
                """
            )

    def load_history(self, session_id: str) -> list[HistoryMessage]:
        """按写入顺序读取指定会话的历史消息。"""
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT role, content FROM chat_messages
                WHERE session_id = ?
                ORDER BY id
                """,
                (session_id,),
            )
            return [{"role": role, "content": content} for role, content in rows]

    def save_turn(self, session_id: str, user_message: str, assistant_reply: str) -> None:
        """在同一事务内保存一轮用户消息和助手回复。"""
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.executemany(
                "INSERT INTO chat_messages (session_id, role, content) VALUES (?, ?, ?)",
                (
                    (session_id, "user", user_message),
                    (session_id, "assistant", assistant_reply),
                ),
            )
