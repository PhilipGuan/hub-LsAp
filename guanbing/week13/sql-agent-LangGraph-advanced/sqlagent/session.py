"""会话与检查点（LangGraph Checkpointer）工厂。

进阶点之一就是**持久化**：给图接上 `checkpointer` 后，中间状态会按 `thread_id`
落进检查点库，于是同一「会话线程」的多轮对话、被 `interrupt()` 暂停的图都能续跑。

- `kind="memory"` → `InMemorySaver`：零依赖，同进程内可恢复（默认）；
- `kind="sqlite"` → `SqliteSaver`：落盘到文件，**跨进程重启也能恢复**。
  需要额外安装：`pip install langgraph-checkpoint-sqlite`。
"""

from __future__ import annotations

import uuid
from typing import Any

DEFAULT_MEMORY = "memory"
DEFAULT_SQLITE = "sqlite"


def make_checkpointer(kind: str = DEFAULT_MEMORY, path: str = "checkpoints.sqlite") -> Any:
    """按 `kind` 造一个 checkpointer。

    Args:
        kind: `"memory"`（默认）或 `"sqlite"`。
        path: 仅 `sqlite` 时使用，检查点数据库文件路径（不存在会自动创建）。
    """
    kind = (kind or DEFAULT_MEMORY).lower()
    if kind == DEFAULT_MEMORY:
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()

    if kind == DEFAULT_SQLITE:
        try:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as e:  # pragma: no cover - 取决于是否装了可选依赖
            raise RuntimeError(
                "使用 sqlite 持久化需先安装：pip install langgraph-checkpoint-sqlite"
            ) from e
        # 允许跨线程复用（notebook / CLI 里常见）
        conn = sqlite3.connect(path, check_same_thread=False)
        return SqliteSaver(conn)

    raise ValueError(f"未知的 checkpointer 类型：{kind!r}（可选：memory / sqlite）")


def new_thread_id(prefix: str = "thread") -> str:
    """生成一个会话线程 ID（图的所有检查点都挂在它下面）。"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def thread_config(thread_id: str, **extra: Any) -> dict:
    """把 `thread_id` 包成图的 `config`（LangGraph 约定的 configurable 形式）。"""
    return {"configurable": {"thread_id": thread_id, **extra}}
