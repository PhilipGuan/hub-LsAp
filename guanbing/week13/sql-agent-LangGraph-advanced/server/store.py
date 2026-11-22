"""状态与准入的 SQLite 落地（D8 / D11 / D14）。

三张表（与检查点**同库不同表**）：

| 表 | 作用 | 决策 |
|---|---|---|
| `sessions` | `session_id` → `thread_id` / 路线 / 人工复核 / **语言** / 轮数 | D8：`session_id` 对外（可轮换），`thread_id` 对内（图主键），两者**解耦** |
| `quota` | 按天累计的全局轮数 | D11：每日全局 200 轮 |
| `audit` | 每轮一行 | D14：供成本快照脚本读 |

检查点的表（`checkpoints` / `writes`）由 `SqliteSaver` 自己建（`sqlagent/session.py`），本模块不碰它 ——
`sqlagent/` 核心库在本批一行不动（D4）。两边共用一个文件，所以连接开 WAL + `busy_timeout`
（`PRODUCTION.md` §7 风险 4 的处置）。

时间一律用 UTC（`time.gmtime`）并取定长格式 `YYYY-MM-DDTHH:MM:SSZ`：
字典序即时间序，「按天」与「7 天过期」都能直接用字符串比较，不需要额外解析。

一个进程内用一把锁把写操作串行化：`--workers 1`（D2）下这就是全部并发，
不必再和 SQLite 的锁升级语义较劲。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SESSION_TTL_DAYS = 7          # D8：7 天未访问即清理
GLOBAL_DAILY_ROUNDS = 200     # D11：每日全局 200 轮
SESSION_ROUNDS = 20           # D11：单会话 20 轮

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL,
    route        TEXT NOT NULL,
    human_review INTEGER NOT NULL DEFAULT 0,
    lang         TEXT NOT NULL DEFAULT 'zh',
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    rounds       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS quota (
    day    TEXT PRIMARY KEY,
    rounds INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    trace_id   TEXT,
    session_id TEXT,
    route      TEXT,
    status     TEXT,
    degraded   INTEGER NOT NULL DEFAULT 0,
    elapsed_ms REAL,
    sql        TEXT,
    rows       INTEGER,
    error      TEXT
);
"""

# 老库升级：`lang` 列是后加的，`CREATE TABLE IF NOT EXISTS` 不会给它补列，这里显式迁移。
# 默认 `zh` —— 既有的 `sessions` 行（升级前建的会话）语言不变。
_SCHEMA_MIGRATIONS = (
    ("sessions", "lang", "ALTER TABLE sessions ADD COLUMN lang TEXT NOT NULL DEFAULT 'zh'"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    """给早于「双语」版本的状态库补列（幂等：列已存在则跳过）。"""
    for table, column, ddl in _SCHEMA_MIGRATIONS:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(ddl)


def connect(path: str | Path) -> sqlite3.Connection:
    """打开状态库的连接。

    - `check_same_thread=False`：FastAPI 的同步路由跑在线程池里，同一连接会被多个线程用；
    - `journal_mode=WAL`：读不阻塞写、写不阻塞读（检查点与审计/配额交错写入）；
      这是**文件级**设置，设一次即对同库的所有连接生效（含 `SqliteSaver` 的连接）；
    - `busy_timeout`：真撞上写锁时等待而不是立刻报 `database is locked`。
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def probe_writable(conn: sqlite3.Connection) -> bool:
    """状态库能不能写（`/health` 的判据，D16）。

    做法：把 `user_version` **写回原值**——语义上什么都没变，但 SQLite 必须真的落一次盘，
    于是「挂卷只读 / 磁盘满 / 权限不对」这类部署事故会被当场暴露，而不是等用户提问时才炸。
    比「往业务表插一行再删」温和：不动任何业务数据，也不会把 `audit` / `quota` 搅脏。

    一个例外：报 `locked` / `busy` 时判**可写**。库里被别的写事务占着，恰恰说明它是可写的；
    否则 `/health` 会在并发下抖动误报（`busy_timeout=5000` 已先等过一轮）。
    """
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        conn.execute(f"PRAGMA user_version={version}")
        return True
    except sqlite3.Error as exc:
        detail = str(exc).lower()
        return "locked" in detail or "busy" in detail


def _now(offset_days: int = 0) -> str:
    """UTC 时间戳（定长），`offset_days` 用于算过期线 / 造过期数据。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_days * 86400))


def _today() -> str:
    """配额按「天」切分，用 UTC 日期。"""
    return time.strftime("%Y-%m-%d", time.gmtime())


class Store:
    """会话元数据 / 配额 / 审计的读写。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(_SCHEMA)
            _migrate(self.conn)
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def writable(self) -> bool:
        """状态库可写性（`/health` 用）。

        探测**必须在 `self.lock` 里做**：连接是跨线程共享的，而 `PRAGMA user_version=…` 是一次
        真写 —— 让别的线程在同一个连接上穿插读写，正是并发压测里第一批炸出来的东西。
        """
        with self.lock:
            return probe_writable(self.conn)

    # ------------------------------------------------------------------ #
    # 会话元数据（D8）
    # ------------------------------------------------------------------ #

    def create_session(self, session_id: str, thread_id: str, route: str,
                       human_review: bool, *, lang: str = "zh",
                       created_at: str | None = None) -> None:
        ts = created_at or _now()
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO sessions"
                " (session_id, thread_id, route, human_review, lang, created_at, last_seen_at,"
                " rounds)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (session_id, thread_id, route, int(bool(human_review)), lang, ts, ts),
            )
            self.conn.commit()

    def get_session(self, session_id: str, *, cutoff: str | None = None) -> dict | None:
        """取会话元数据；超过保留期的按「不存在」处理（与启动时的清理同一把尺子）。"""
        cutoff = cutoff or _now(-SESSION_TTL_DAYS)
        with self.lock:
            row = self.conn.execute(
                "SELECT session_id, thread_id, route, human_review, lang, created_at,"
                " last_seen_at, rounds FROM sessions WHERE session_id = ? AND last_seen_at >= ?",
                (session_id, cutoff),
            ).fetchone()
        if row is None:
            return None
        keys = ("session_id", "thread_id", "route", "human_review", "lang", "created_at",
                "last_seen_at", "rounds")
        meta = dict(zip(keys, row))
        meta["human_review"] = bool(meta["human_review"])
        return meta

    def touch(self, session_id: str, *, now: str | None = None) -> None:
        with self.lock:
            self.conn.execute("UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
                              (now or _now(), session_id))
            self.conn.commit()

    def prune_expired(self, *, cutoff: str | None = None) -> list[str]:
        """删掉超期会话元数据，返回**应连带清理检查点**的 `thread_id` 列表。

        检查点表归 `SqliteSaver` 管，这里只负责报出要删哪些线程（`delete_thread()` 由调用方执行）。
        """
        cutoff = cutoff or _now(-SESSION_TTL_DAYS)
        with self.lock:
            rows = self.conn.execute(
                "SELECT thread_id FROM sessions WHERE last_seen_at < ?", (cutoff,),
            ).fetchall()
            self.conn.execute("DELETE FROM sessions WHERE last_seen_at < ?", (cutoff,))
            self.conn.commit()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ #
    # 配额（D11）
    # ------------------------------------------------------------------ #

    def reserve_round(self, session_id: str) -> str | None:
        """配额闸门：**先留一格再放行**（本轮若跑失败，额度也算花掉了——真花了 token）。

        返回 `None` 表示放行；返回 `"global"` / `"session"` 表示该维度已用尽，
        由 `app.py` 翻译成话术（`messages.py`）。
        """
        day = _today()
        with self.lock:
            row = self.conn.execute("SELECT rounds FROM quota WHERE day = ?", (day,)).fetchone()
            if (row[0] if row else 0) >= GLOBAL_DAILY_ROUNDS:
                return "global"
            row = self.conn.execute("SELECT rounds FROM sessions WHERE session_id = ?",
                                    (session_id,)).fetchone()
            if (row[0] if row else 0) >= SESSION_ROUNDS:
                return "session"
            self.conn.execute(
                "INSERT INTO quota (day, rounds) VALUES (?, 1)"
                " ON CONFLICT(day) DO UPDATE SET rounds = rounds + 1", (day,),
            )
            self.conn.execute(
                "UPDATE sessions SET rounds = rounds + 1, last_seen_at = ? WHERE session_id = ?",
                (_now(), session_id),
            )
            self.conn.commit()
        return None

    # ------------------------------------------------------------------ #
    # 审计（D14）
    # ------------------------------------------------------------------ #

    def record_audit(self, *, trace_id: str, session_id: str, route: str, status: str,
                     degraded: bool, elapsed_ms: float, sql: str | None = None,
                     rows: int | None = None, error: str | None = None,
                     now: str | None = None) -> None:
        """写一行本轮审计。字段口径与 JSON 行日志（`_emit`）保持一致。

        `elapsed_ms / sql / rows / error` 是本轮结果的**边界层视角**；
        token 与 LLM 调用次数要挂在模型回调上（进程内才拿得到），留给批 4 的成本快照脚本。
        """
        with self.lock:
            self.conn.execute(
                "INSERT INTO audit (ts, trace_id, session_id, route, status, degraded,"
                " elapsed_ms, sql, rows, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now or _now(), trace_id, session_id, route, status, int(bool(degraded)),
                 elapsed_ms, sql, rows, error),
            )
            self.conn.commit()
