"""SQLite persistence and FTS5 keyword index."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import DB_PATH


STATUS_PENDING = "pending"
STATUS_PARSING = "parsing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    stored_path   TEXT NOT NULL,
    size          INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT,
    title         TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    progress      INTEGER NOT NULL DEFAULT 0,
    stage         TEXT NOT NULL DEFAULT '等待解析',
    tier          TEXT,
    error         TEXT,
    page_count    INTEGER NOT NULL DEFAULT 0,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    image_count   INTEGER NOT NULL DEFAULT 0,
    parse_seconds REAL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_created ON documents(created_at DESC);

CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    page        INTEGER NOT NULL DEFAULT 1,
    heading     TEXT,
    content     TEXT NOT NULL,
    image_path  TEXT,
    caption     TEXT,
    tokens      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_chunks_kind ON chunks(kind);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    tokens,
    tokenize='unicode61 remove_diacritics 2'
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.executescript(SCHEMA)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def create_document(
    *,
    document_id: str,
    filename: str,
    stored_path: str,
    size: int,
    sha256: str,
    tier: str,
) -> dict[str, Any]:
    timestamp = now_iso()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO documents
                (id, filename, stored_path, size, sha256, tier, status, progress,
                 stage, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                document_id,
                filename,
                stored_path,
                size,
                sha256,
                tier,
                STATUS_PENDING,
                "排队中",
                timestamp,
                timestamp,
            ),
        )
    return get_document(document_id)  # type: ignore[return-value]


def get_document(document_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        return _row(
            conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        )


def list_documents(limit: int = 200) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def update_document(document_id: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {
        "title",
        "status",
        "progress",
        "stage",
        "tier",
        "error",
        "page_count",
        "chunk_count",
        "image_count",
        "parse_seconds",
        "completed_at",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [document_id]
    with connection() as conn:
        conn.execute(
            f"UPDATE documents SET {assignments} WHERE id = ?",  # keys are whitelisted
            values,
        )
    return get_document(document_id)


def replace_chunks(document_id: str, chunks: list[dict[str, Any]]) -> None:
    """Atomically replace all searchable units for a document."""
    timestamp = now_iso()
    with connection() as conn:
        old_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM chunks WHERE doc_id = ?", (document_id,)
            ).fetchall()
        ]
        if old_ids:
            conn.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id = ?",
                [(chunk_id,) for chunk_id in old_ids],
            )
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (document_id,))
        for chunk in chunks:
            conn.execute(
                """
                INSERT INTO chunks
                    (id, doc_id, ordinal, kind, page, heading, content,
                     image_path, caption, tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk["id"],
                    document_id,
                    chunk["ordinal"],
                    chunk["kind"],
                    chunk.get("page", 1),
                    chunk.get("heading"),
                    chunk["content"],
                    chunk.get("image_path"),
                    chunk.get("caption"),
                    chunk.get("tokens", ""),
                    timestamp,
                ),
            )
            conn.execute(
                "INSERT INTO chunks_fts (chunk_id, tokens) VALUES (?, ?)",
                (chunk["id"], chunk.get("tokens", "")),
            )
        conn.execute(
            """
            UPDATE documents
               SET chunk_count = ?, image_count = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                len(chunks),
                sum(1 for chunk in chunks if chunk.get("image_path")),
                timestamp,
                document_id,
            ),
        )


def fts_search(
    match_expression: str,
    doc_ids: list[str] | None,
    *,
    kind: str | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Return chunks ordered best-first. `rank` is the raw FTS5 bm25 value."""
    sql = """
        SELECT c.*, bm25(chunks_fts) AS rank
          FROM chunks_fts
          JOIN chunks c ON c.id = chunks_fts.chunk_id
         WHERE chunks_fts MATCH ?
    """
    params: list[Any] = [match_expression]
    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        sql += f" AND c.doc_id IN ({placeholders})"
        params.extend(doc_ids)
    if kind:
        sql += " AND c.kind = ?"
        params.append(kind)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def fallback_chunks(
    doc_ids: list[str] | None, limit: int = 8
) -> list[dict[str, Any]]:
    sql = "SELECT *, 0.0 AS rank FROM chunks"
    params: list[Any] = []
    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        sql += f" WHERE doc_id IN ({placeholders})"
        params.extend(doc_ids)
    sql += " ORDER BY created_at DESC, ordinal ASC LIMIT ?"
    params.append(limit)
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def images_for_documents(
    doc_ids: list[str] | None, limit: int = 12
) -> list[dict[str, Any]]:
    sql = """
        SELECT c.*, d.filename, d.title
          FROM chunks c JOIN documents d ON d.id = c.doc_id
         WHERE c.image_path IS NOT NULL
    """
    params: list[Any] = []
    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        sql += f" AND c.doc_id IN ({placeholders})"
        params.extend(doc_ids)
    sql += " ORDER BY c.created_at DESC, c.ordinal ASC LIMIT ?"
    params.append(limit)
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def document_detail(document_id: str) -> dict[str, Any] | None:
    document = get_document(document_id)
    if not document:
        return None
    with connection() as conn:
        images = conn.execute(
            """
            SELECT id, page, heading, content, image_path, caption
              FROM chunks
             WHERE doc_id = ? AND image_path IS NOT NULL
             ORDER BY ordinal
            """,
            (document_id,),
        ).fetchall()
    markdown_path = Path(document["stored_path"]).parent.parent / "parsed" / document_id / "markdown.md"
    excerpt = ""
    if markdown_path.exists():
        excerpt = markdown_path.read_text(encoding="utf-8", errors="ignore")[:6000]
    document["images"] = [dict(row) for row in images]
    document["excerpt"] = excerpt
    return document


def delete_document(document_id: str) -> dict[str, Any] | None:
    document = get_document(document_id)
    if not document:
        return None
    with connection() as conn:
        chunk_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM chunks WHERE doc_id = ?", (document_id,)
            ).fetchall()
        ]
        if chunk_ids:
            conn.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id = ?",
                [(chunk_id,) for chunk_id in chunk_ids],
            )
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    return document


def stats() -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS documents,
                COALESCE(SUM(status = 'completed'), 0) AS completed,
                COALESCE(SUM(status = 'pending'), 0) AS pending,
                COALESCE(SUM(status = 'parsing'), 0) AS parsing,
                COALESCE(SUM(status = 'failed'), 0) AS failed,
                COALESCE(SUM(page_count), 0) AS pages,
                COALESCE(SUM(chunk_count), 0) AS chunks,
                COALESCE(SUM(image_count), 0) AS images
              FROM documents
            """
        ).fetchone()
    return dict(row) if row else {}


def chunks_by_document(document_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY ordinal", (document_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def markdown_for_document(document_id: str) -> str:
    document = get_document(document_id)
    if not document:
        return ""
    path = Path(document["stored_path"]).parent.parent / "parsed" / document_id / "markdown.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
