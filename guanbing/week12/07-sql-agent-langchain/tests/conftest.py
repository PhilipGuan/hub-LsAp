"""测试公共夹具：全部**离线**运行，零网络调用。

关键手法：把 `sqlagent.llm` 的两个入口（`invoke_messages` / `invoke_structured_messages`）
换成假实现 —— 因为包内所有模块都是 `from .. import llm` 后以 `llm.xxx()` 调用，
所以 `monkeypatch.setattr(llm, "invoke_messages", fake)` 一处生效、全线生效。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sqlagent.db import DBParser

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHINOOK = PROJECT_ROOT / "chinook.db"


@pytest.fixture(scope="session")
def parser() -> DBParser:
    """真实 chinook.db 的解析器（只读，跑在内存外的真实文件上）。"""
    return DBParser(f"sqlite:///{CHINOOK}", verbose=False)


def make_text_responder(responses: list[str]):
    """按调用顺序返回预设文本；用尽后抛错，暴露「多调用了一次」。"""
    it = iter(responses)

    def responder(messages, **kwargs):
        try:
            return next(it)
        except StopIteration:  # pragma: no cover - 仅用于定位测试写错
            raise AssertionError(f"Llm 文本调用次数超出预期（已用 {len(responses)} 次）") from None

    return responder


def make_structured_responder(handlers: dict):
    """按 schema 类型分发结构化输出；未注册的类型返回 None（模拟「解析失败」）。"""

    def responder(schema, messages, **kwargs):
        handler = handlers.get(schema)
        return handler(schema, messages) if handler else None

    return responder
