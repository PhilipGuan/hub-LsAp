"""事件信封 —— CLI 与 SSE 共用的那一层。

``engine.run_research`` 是个异步生成器，吐出来的就是 ``Event``。CLI 直接打印它，
``api.py`` 把它转成 SSE 帧。**两边看到的是同一串事件**，不会出现「CLI 能看到的
进度 SSE 看不到」这种两套实现跑偏的情况。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    run_started = "run_started"
    status = "status"
    plan = "plan"
    search = "search"
    search_results = "search_results"
    fetch = "fetch"
    fetch_failed = "fetch_failed"
    note = "note"
    reflect = "reflect"
    round_end = "round_end"
    synthesize_start = "synthesize_start"
    section = "section"
    report = "report"
    confidence = "confidence"
    stop = "stop"
    done = "done"
    error = "error"
    cancelled = "cancelled"
    hello = "hello"  # 仅 SSE：建连时的握手帧


# 这些事件只影响进度显示，不改变研究结果 —— SSE 断线重连时可以不重放
TRANSIENT_TYPES: frozenset[EventType] = frozenset({EventType.status})


class Event(BaseModel):
    seq: int = 0
    run_id: str = ""
    type: EventType
    round: int = 0
    ts: datetime = Field(default_factory=datetime.now)
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        """转成 SSE 帧。

        ``event:`` 让前端能按类型挂监听，``data:`` 是完整信封（含 seq，
        断线重连时靠它对齐 ``Last-Event-ID``）。
        """
        body = self.model_dump_json()
        return f"id: {self.seq}\nevent: {self.type.value}\ndata: {body}\n\n"


def make_event(
    event_type: EventType,
    *,
    run_id: str = "",
    round_no: int = 0,
    seq: int = 0,
    **payload: Any,
) -> Event:
    return Event(seq=seq, run_id=run_id, type=event_type, round=round_no, payload=payload)


__all__ = ["TRANSIENT_TYPES", "Event", "EventType", "make_event"]
