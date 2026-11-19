"""多轮对话历史的格式化工具（进程2 的提示词里使用）。"""
from __future__ import annotations

from typing import Any


def format_history(history: Any, limit: int = 4) -> str:
    """把 [{"user": ..., "assistant": ...}] 转成提示词里的对话块。"""
    if not history:
        return ""

    lines: list[str] = []
    for turn in list(history)[-limit:]:
        if not isinstance(turn, dict):
            continue
        user = str(turn.get("user", "")).strip()
        assistant = str(turn.get("assistant", "")).strip()
        if user:
            lines.append(f"用户：{user}")
        if assistant:
            lines.append(f"助手：{assistant}")
    return "\n".join(lines)
