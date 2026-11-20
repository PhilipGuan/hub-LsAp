# -*- coding: utf-8 -*-
"""SQLite 元数据存储。

记录每个上传的 PDF 文档及其解析状态，供前端轮询展示：
    待解析 -> 解析中 -> 解析完成 / 解析失败
"""

import sqlite3
import time
import uuid
from contextlib import contextmanager

from app.config import SQLITE_PATH, STATUS_PENDING

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    status      TEXT NOT NULL,
    error_msg   TEXT,
    chunk_count INTEGER DEFAULT 0,
    image_count INTEGER DEFAULT 0,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(_SCHEMA)


def create_document(filename, file_path, doc_id=None):
    """插入一条新文档记录，初始状态为待解析，返回 doc_id。"""
    doc_id = doc_id or uuid.uuid4().hex
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (doc_id, filename, file_path, status, "
            "chunk_count, image_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
            (doc_id, filename, file_path, STATUS_PENDING, now, now),
        )
    return doc_id


def update_status(doc_id, status, error_msg=None, chunk_count=None, image_count=None):
    fields = ["status = ?", "updated_at = ?"]
    values = [status, int(time.time())]
    if error_msg is not None:
        fields.append("error_msg = ?")
        values.append(error_msg)
    if chunk_count is not None:
        fields.append("chunk_count = ?")
        values.append(chunk_count)
    if image_count is not None:
        fields.append("image_count = ?")
        values.append(image_count)
    values.append(doc_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE documents SET {', '.join(fields)} WHERE doc_id = ?", values)


def get_document(doc_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None


def list_documents():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    did = create_document("demo.pdf", "/tmp/demo.pdf")
    print("created:", get_document(did))
    update_status(did, "解析中")
    print("updated:", get_document(did))
