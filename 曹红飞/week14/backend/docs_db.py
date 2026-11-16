"""文档解析状态的本地存储（sqlite3）。"""
import datetime
import sqlite3
from typing import Dict, List, Optional

from . import config

STATUS_PENDING = "pending"   # 待解析
STATUS_PARSING = "parsing"   # 解析中
STATUS_DONE = "done"         # 解析完成
STATUS_FAILED = "failed"     # 解析失败

_STATUS_LABELS = {
    STATUS_PENDING: "待解析",
    STATUS_PARSING: "解析中",
    STATUS_DONE: "解析完成",
    STATUS_FAILED: "解析失败",
}


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DOCS_DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表（幂等）。"""
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def add_doc(doc_id: str, filename: str) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, filename, status, error, created_at, updated_at) "
            "VALUES (?, ?, ?, '', ?, ?)",
            (doc_id, filename, STATUS_PENDING, now, now),
        )


def update_status(doc_id: str, status: str, error: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE documents SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, _now(), doc_id),
        )


def get_doc(doc_id: str) -> Optional[Dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    return dict(row) if row else None


def list_docs() -> List[Dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY created_at DESC"
        ).fetchall()
    docs = []
    for row in rows:
        doc = dict(row)
        doc["status_label"] = _STATUS_LABELS.get(doc["status"], doc["status"])
        docs.append(doc)
    return docs
