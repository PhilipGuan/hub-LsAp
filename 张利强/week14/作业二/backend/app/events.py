"""进程内事件总线：worker 通过 Kafka 把状态发到 API，API 再广播给 SSE 订阅者。"""

import asyncio
import json
import threading
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish_threadsafe(self, payload: dict[str, Any]) -> None:
        """可被任意线程调用（如 Kafka 消费线程）。"""
        if self._loop is None:
            return
        data = json.dumps(payload, ensure_ascii=False)
        self._loop.call_soon_threadsafe(self._publish_now, data)

    def _publish_now(self, data: str) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass


bus = EventBus()
