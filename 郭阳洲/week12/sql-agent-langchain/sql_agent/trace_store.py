from __future__ import annotations

import json
import re
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

TRACE_PREFIX = "tr_"
USER_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,16}$")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def make_trace_id() -> str:
    return datetime.now().strftime("tr_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]


def normalize_user_code(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return "local"
    if not USER_CODE_PATTERN.match(text):
        raise ValueError("用户编码需为 2-16 位字母、数字、下划线或短横线")
    return text


class TraceStore(Protocol):
    def write(self, event: dict[str, Any]) -> bool: ...

    def read(self, trace_id: str) -> dict[str, Any] | None: ...

    def recent(self, limit: int = 20) -> list[dict[str, Any]]: ...


class NullTraceStore:
    def write(self, event: dict[str, Any]) -> bool:
        return True

    def read(self, trace_id: str) -> dict[str, Any] | None:
        return None

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return []


class JsonlTraceStore:
    """追加写 JSONL；写失败只降级，不阻断答案。"""

    def __init__(self, directory: str | Path, retention_days: int = 30):
        self.directory = Path(directory)
        self.retention_days = max(1, int(retention_days))
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cleanup()

    def _file_for(self, day: date | None = None) -> Path:
        day = day or date.today()
        return self.directory / f"traces-{day.strftime('%Y%m%d')}.jsonl"

    def _cleanup(self) -> None:
        cutoff = date.today() - timedelta(days=self.retention_days)
        for path in self.directory.glob("traces-*.jsonl"):
            match = re.match(r"traces-(\d{8})\.jsonl$", path.name)
            if not match:
                continue
            try:
                day = datetime.strptime(match.group(1), "%Y%m%d").date()
            except ValueError:
                continue
            if day < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    def write(self, event: dict[str, Any]) -> bool:
        payload = _json_safe(event)
        payload.setdefault("timestamp", now_iso())
        try:
            with self._file_for().open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                handle.flush()
            return True
        except (OSError, TypeError, ValueError) as exc:
            print(f"[audit] E8009 JSONL 写入失败: {exc}", file=sys.stderr)
            return False

    def read(self, trace_id: str) -> dict[str, Any] | None:
        for path in sorted(self.directory.glob("traces-*.jsonl"), reverse=True):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("trace_id") == trace_id:
                    return event
        return None

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("traces-*.jsonl"), reverse=True):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(events) >= limit:
                    return events
        return events


class FeedbackStore:
    """赞/踩需要更新，因此用 SQLite；trace 仍是追加写 JSONL。"""

    def __init__(self, path: str | Path, retention_days: int = 30):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days = max(1, int(retention_days))
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    user_code TEXT NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating IN (-1, 1)),
                    reason TEXT DEFAULT '',
                    question TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (trace_id, user_code)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at)"
            )
            conn.execute(
                "DELETE FROM feedback WHERE created_at < datetime('now', ?)",
                (f"-{self.retention_days} days",),
            )

    def save(
        self,
        *,
        trace_id: str,
        user_code: str,
        rating: int,
        reason: str = "",
        question: str = "",
    ) -> None:
        if rating not in (-1, 1):
            raise ValueError("rating 只能是 1 或 -1")
        user_code = normalize_user_code(user_code)
        stamp = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO feedback
                    (trace_id, user_code, rating, reason, question, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trace_id, user_code) DO UPDATE SET
                    rating = excluded.rating,
                    reason = excluded.reason,
                    question = excluded.question,
                    updated_at = excluded.updated_at
                """,
                (trace_id, user_code, rating, reason[:500], question[:500], stamp, stamp),
            )

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT trace_id, user_code, rating, reason, question, created_at, updated_at "
                "FROM feedback ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [
            {
                "trace_id": row[0],
                "user_code": row[1],
                "rating": row[2],
                "reason": row[3],
                "question": row[4],
                "created_at": row[5],
                "updated_at": row[6],
            }
            for row in rows
        ]

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(rating = 1), 0), "
                "COALESCE(SUM(rating = -1), 0) FROM feedback"
            ).fetchone()
        total, up, down = (row or (0, 0, 0))
        return {
            "total": int(total),
            "up": int(up),
            "down": int(down),
            "positive_rate": round(up / total, 4) if total else None,
        }


def fingerprint(
    *,
    model: str,
    prompt_version: str,
    metrics_version: str,
    aliases_version: str,
    catalog_fingerprint: str,
    app_version: str = "mvp-1",
) -> dict[str, str]:
    return {
        "app": app_version,
        "model": model,
        "prompt": prompt_version,
        "metrics": metrics_version,
        "aliases": aliases_version,
        "catalog": catalog_fingerprint,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
