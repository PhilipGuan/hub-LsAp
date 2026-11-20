"""SQLite 持久化层。

设计要点
--------
* 只用标准库 ``sqlite3``，不引入额外组件；开启 WAL 后 Web 进程和解析
  工作进程可以同时读写同一个库。
* 每线程一个连接（``new_conn()``），写操作都很短，靠 ``busy_timeout`` 兜底。
* 文档状态机：``pending`` → ``parsing`` → ``done`` / ``failed``。
* 所有 SQL 都是固定语句 + 参数绑定，不做字符串拼接。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from .config import settings

# --------------------------------------------------------------------------- #
# 状态常量：与前端展示一一对应
# --------------------------------------------------------------------------- #
STATUS_PENDING = "pending"
STATUS_PARSING = "parsing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

STATUS_LABELS = {
    STATUS_PENDING: "待解析",
    STATUS_PARSING: "解析中",
    STATUS_DONE: "解析完成",
    STATUS_FAILED: "解析失败",
}
TERMINAL_STATUSES = (STATUS_DONE, STATUS_FAILED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    stored_path   TEXT NOT NULL,
    size          INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT NOT NULL DEFAULT '',
    page_count    INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending',
    progress      REAL NOT NULL DEFAULT 0,
    stage         TEXT NOT NULL DEFAULT '',
    parser        TEXT NOT NULL DEFAULT '',
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    figure_count  INTEGER NOT NULL DEFAULT 0,
    summary       TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'kafka',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_created ON documents(created_at DESC);

CREATE TABLE IF NOT EXISTS chunks (
    id         TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL,
    kind       TEXT NOT NULL,              -- text | figure | table
    ord        INTEGER NOT NULL DEFAULT 0,
    page       INTEGER NOT NULL DEFAULT 1,
    page_end   INTEGER NOT NULL DEFAULT 1,
    section    TEXT NOT NULL DEFAULT '',
    bbox       TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    caption    TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    image_url  TEXT NOT NULL DEFAULT '',
    width      INTEGER NOT NULL DEFAULT 0,
    height     INTEGER NOT NULL DEFAULT 0,
    n_chars    INTEGER NOT NULL DEFAULT 0,
    embedding  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_kind ON chunks(kind);

CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    content_md   TEXT NOT NULL DEFAULT '',
    content_html TEXT NOT NULL DEFAULT '',
    images       TEXT NOT NULL DEFAULT '[]',
    sources      TEXT NOT NULL DEFAULT '[]',
    provider     TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    elapsed_ms   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS document_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id     TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_doc ON document_events(doc_id, id);

CREATE TABLE IF NOT EXISTS runtime_kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}{raw}" if prefix else raw


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dump_list(value: Sequence[dict[str, Any]] | None) -> str:
    try:
        return json.dumps(list(value or []), ensure_ascii=False)
    except (TypeError, ValueError):
        return "[]"


def _load_list(text: Any) -> list[dict[str, Any]]:
    try:
        data = json.loads(text) if text else []
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
def new_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """短事务上下文：正常提交，异常回滚。"""
    conn = new_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = new_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #
def create_document(
    *,
    filename: str,
    stored_path: str,
    size: int,
    sha256: str,
    source: str,
    status: str = STATUS_PENDING,
    doc_id: str | None = None,
) -> dict[str, Any]:
    doc_id = doc_id or new_id("doc_")
    ts = now()
    with tx() as conn:
        conn.execute(
            """INSERT INTO documents
               (id, filename, stored_path, size, sha256, status, progress, stage,
                source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, filename, stored_path, size, sha256, status, 0.0, "等待解析",
             source, ts, ts),
        )
    doc = get_document(doc_id)
    if doc is None:  # pragma: no cover - 理论上不会发生
        raise RuntimeError("文档写入失败")
    return doc


#: 允许通过 update_document 更新的列（白名单，防注入）
UPDATABLE_COLUMNS = frozenset(
    {
        "filename", "stored_path", "size", "sha256", "page_count", "status",
        "progress", "stage", "parser", "chunk_count", "figure_count",
        "summary", "error", "source", "started_at", "finished_at",
    }
)

_UPDATE_SQL = """
UPDATE documents SET
    filename     = ?,
    stored_path  = ?,
    size         = ?,
    sha256       = ?,
    page_count   = ?,
    status       = ?,
    progress     = ?,
    stage        = ?,
    parser       = ?,
    chunk_count  = ?,
    figure_count = ?,
    summary      = ?,
    error        = ?,
    source       = ?,
    started_at   = ?,
    finished_at  = ?,
    updated_at   = ?
WHERE id = ?
"""

_UPDATE_KEYS = (
    "filename", "stored_path", "size", "sha256", "page_count", "status",
    "progress", "stage", "parser", "chunk_count", "figure_count", "summary",
    "error", "source", "started_at", "finished_at",
)


def update_document(doc_id: str, **fields: Any) -> None:
    """按字段更新；未传的字段保持原值。

    采用「读取-合并-整行 UPDATE」的方式，SQL 是固定语句，避免拼接列名。
    """
    unknown = set(fields) - UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"不允许更新的字段: {sorted(unknown)}")

    current = get_document(doc_id)
    if current is None:
        return

    merged: dict[str, Any] = {key: current.get(key) for key in _UPDATE_KEYS}
    merged.update(fields)

    if merged.get("status") == STATUS_PARSING and not merged.get("started_at"):
        merged["started_at"] = now()
    if merged.get("status") in TERMINAL_STATUSES and not merged.get("finished_at"):
        merged["finished_at"] = now()

    values = [merged.get(key) for key in _UPDATE_KEYS]
    with tx() as conn:
        conn.execute(_UPDATE_SQL, [*values, now(), doc_id])


def get_document(doc_id: str) -> dict[str, Any] | None:
    with tx() as conn:
        return _row(conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())


def list_documents(limit: int = 100) -> list[dict[str, Any]]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def find_document_by_hash(sha256: str) -> dict[str, Any] | None:
    with tx() as conn:
        return _row(
            conn.execute(
                "SELECT * FROM documents WHERE sha256 = ? AND status != ? LIMIT 1",
                (sha256, STATUS_FAILED),
            ).fetchone()
        )


def delete_document(doc_id: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM document_events WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def document_counts() -> dict[str, int]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM documents GROUP BY status"
        ).fetchall()
    counts: dict[str, int] = dict.fromkeys(STATUS_LABELS, 0)
    for row in rows:
        counts[str(row["status"])] = _as_int(row["n"])
    counts["total"] = sum(counts[status] for status in STATUS_LABELS)
    return counts


# --------------------------------------------------------------------------- #
# chunks
# --------------------------------------------------------------------------- #
def replace_chunks(doc_id: str, chunks: Sequence[dict[str, Any]]) -> None:
    """整篇替换，保证重复解析不会产生脏数据。"""
    with tx() as conn:
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            """INSERT INTO chunks
               (id, doc_id, kind, ord, page, page_end, section, bbox, content,
                caption, image_path, image_url, width, height, n_chars, embedding)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    chunk["id"], doc_id, chunk["kind"], chunk["ord"], chunk["page"],
                    chunk.get("page_end", chunk["page"]), chunk.get("section", ""),
                    chunk.get("bbox", ""), chunk.get("content", ""),
                    chunk.get("caption", ""), chunk.get("image_path", ""),
                    chunk.get("image_url", ""), chunk.get("width", 0),
                    chunk.get("height", 0), len(chunk.get("content", "")),
                    chunk.get("embedding", ""),
                )
                for chunk in chunks
            ],
        )


def fetch_chunks(doc_ids: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """按文档范围取块；doc_ids 为空表示全部已完成文档。"""
    if not doc_ids:
        with tx() as conn:
            rows = conn.execute(
                """SELECT c.*, d.filename, d.status FROM chunks c
                   JOIN documents d ON d.id = c.doc_id
                   WHERE d.status = ?
                   ORDER BY c.doc_id, c.ord""",
                (STATUS_DONE,),
            ).fetchall()
        return [dict(row) for row in rows]

    # json_each 让 IN 查询保持单参数绑定，占位符只有 ?
    scope = json.dumps(list(doc_ids), ensure_ascii=False)
    with tx() as conn:
        rows = conn.execute(
            """SELECT c.*, d.filename, d.status FROM chunks c
               JOIN documents d ON d.id = c.doc_id
               WHERE d.status = ?
                 AND c.doc_id IN (SELECT value FROM json_each(?))
               ORDER BY c.doc_id, c.ord""",
            (STATUS_DONE, scope),
        ).fetchall()
    return [dict(row) for row in rows]


def chunks_fingerprint() -> tuple[int, int]:
    """索引指纹：(块数, 最大 rowid)。用于跨进程感知索引是否过期。"""
    with tx() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS m FROM chunks"
        ).fetchone()
    if row is None:  # pragma: no cover
        return (0, 0)
    return (_as_int(row["n"]), _as_int(row["m"]))


def chunk_count(doc_id: str) -> int:
    with tx() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE doc_id = ?", (doc_id,)
        ).fetchone()
    return _as_int(row["n"]) if row else 0


# --------------------------------------------------------------------------- #
# messages（会话历史）
# --------------------------------------------------------------------------- #
def add_message(
    session_id: str,
    role: str,
    *,
    content_md: str = "",
    content_html: str = "",
    images: Sequence[dict[str, Any]] | None = None,
    sources: Sequence[dict[str, Any]] | None = None,
    provider: str = "",
    model: str = "",
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    message_id = new_id("msg_")
    ts = now()
    image_list = list(images or [])
    source_list = list(sources or [])
    with tx() as conn:
        conn.execute(
            """INSERT INTO messages
               (id, session_id, role, content_md, content_html, images, sources,
                provider, model, elapsed_ms, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                message_id, session_id, role, content_md, content_html,
                _dump_list(image_list), _dump_list(source_list),
                provider, model, elapsed_ms, ts,
            ),
        )
    return {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content_md": content_md,
        "content_html": content_html,
        "images": image_list,
        "sources": source_list,
        "provider": provider,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "created_at": ts,
    }


def list_messages(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with tx() as conn:
        rows = conn.execute(
            """SELECT * FROM messages WHERE session_id = ?
               ORDER BY created_at ASC, rowid ASC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["images"] = _load_list(item.get("images"))
        item["sources"] = _load_list(item.get("sources"))
        result.append(item)
    return result


def clear_messages(session_id: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))


# --------------------------------------------------------------------------- #
# events / runtime kv
# --------------------------------------------------------------------------- #
def add_event(doc_id: str, kind: str, status: str = "", detail: str = "") -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO document_events (doc_id, kind, status, detail, created_at) VALUES (?,?,?,?,?)",
            (doc_id, kind, status, detail[:2000], now()),
        )


def list_events(doc_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT * FROM document_events WHERE doc_id = ? ORDER BY id DESC LIMIT ?",
            (doc_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def set_runtime(key: str, value: str) -> None:
    with tx() as conn:
        conn.execute(
            """INSERT INTO runtime_kv (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                              updated_at = excluded.updated_at""",
            (key, value, now()),
        )


def get_runtime(key: str, default: str = "") -> str:
    with tx() as conn:
        row = conn.execute("SELECT value FROM runtime_kv WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default
