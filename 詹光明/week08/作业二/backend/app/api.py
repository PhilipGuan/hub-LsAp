"""FastAPI 接口层 —— 把 engine 的事件流转成 HTTP + SSE。

**创建与订阅拆成两步**，因为 ``EventSource`` 只支持 GET、带不了请求体：

    POST /api/research            → 202 {run_id}，编排在后台任务里跑
    GET  /api/research/{id}/events → SSE，边跑边推

事件本身**就是 CLI 打印的那一串** —— 引擎吐什么这里推什么，没有第二套实现。
所以「CLI 能看到的进度 SSE 看不到」这种跑偏不会发生。

研究要跑几分钟，客户端刷新是必然场景，所以事件在内存环形缓冲里留一份，
断线重连带 ``Last-Event-ID`` 就能精确续上。

**没有中间产物落盘**（本版明确不做）：进程一重启，跑过的 run 就没了。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .engine import run_research
from .events import TRANSIENT_TYPES, Event, EventType, make_event
from .models import ResearchRequest, RunStatus

logger = logging.getLogger(__name__)

# 编排函数的签名 —— 引擎是异步生成器，测试替身可以是普通 async 生成器
Runner = Callable[[ResearchRequest, str], AsyncIterator[Event]]

# 每个 run 在内存里保留多少条事件。研究报告本身可能很长，
# 但事件流里只有 report 一条是大对象，2000 条足够覆盖一次完整研究。
BUFFER_SIZE = 2000
# 同时保留多少个 run（含已完成的）。内存缓冲不做持久化，就得有个上限。
MAX_RUNS = 50
# SSE 心跳间隔 —— 中间有代理时，太久不发东西连接会被掐掉
PING_INTERVAL_S = 15.0


@dataclass
class RunState:
    """一个 run 的全部内存状态。"""

    run_id: str
    request: ResearchRequest
    status: RunStatus = RunStatus.init
    round: int = 0
    stop_reason: str | None = None
    source_count: int = 0
    note_count: int = 0
    plan: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None

    events: deque[Event] = field(default_factory=lambda: deque(maxlen=BUFFER_SIZE))
    subscribers: set[asyncio.Queue[Event | None]] = field(default_factory=set)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "topic": self.request.topic,
            "status": self.status.value,
            "round": self.round,
            "max_rounds": self.request.max_rounds,
            "stop_reason": self.stop_reason,
            "sources": self.source_count,
            "notes": self.note_count,
            "report_ready": self.report is not None,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "last_seq": self.events[-1].seq if self.events else 0,
        }


def _default_runner(request: ResearchRequest, run_id: str) -> AsyncIterator[Event]:
    """默认就是引擎本身。留成可替换的钩子，测试才能不联网跑整条 HTTP 链路。"""
    return run_research(request, run_id=run_id)


class RunRegistry:
    """内存里所有 run 的登记处。**进程重启即清空。**"""

    def __init__(self, runner: Runner | None = None) -> None:
        self._runs: dict[str, RunState] = {}
        self.runner: Runner = runner or _default_runner

    def get(self, run_id: str) -> RunState:
        state = self._runs.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"未找到 run {run_id}")
        return state

    def create(self, request: ResearchRequest, run_id: str) -> RunState:
        self._evict()
        state = RunState(run_id=run_id, request=request)
        self._runs[run_id] = state
        return state

    def reset(self, runner: Runner | None = None) -> None:
        """清空全部 run —— 供测试隔离用，别在生产路径上调。"""
        self._runs.clear()
        self.runner = runner or _default_runner

    def start(self, state: RunState) -> None:
        state.task = asyncio.create_task(
            _drive(state, self.runner), name=f"research:{state.run_id}"
        )

    async def shutdown(self) -> None:
        for state in self._runs.values():
            if state.task is not None and not state.task.done():
                state.task.cancel()
        pending = [s.task for s in self._runs.values() if s.task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _evict(self) -> None:
        """超出上限时先淘汰最早完成的，保护内存。"""
        if len(self._runs) < MAX_RUNS:
            return
        finished = sorted(
            (s for s in self._runs.values() if s.done.is_set()),
            key=lambda s: s.finished_at or s.created_at,
        )
        for state in finished[: max(1, len(self._runs) - MAX_RUNS + 1)]:
            self._runs.pop(state.run_id, None)


async def _drive(state: RunState, runner: Runner) -> None:
    """把引擎跑完，沿途把事件写进缓冲并广播给订阅者。"""
    try:
        async for event in runner(state.request, state.run_id):
            _record(state, event)
    except asyncio.CancelledError:
        state.status = RunStatus.cancelled
        # 覆盖而不是「没有才填」：跑到一半被掐断时，stop_reason 可能还留着
        # 上一轮反思写下的 sufficient —— 那样快照会自相矛盾。
        # 这个 run 是被取消的，这就是它结束的方式。
        state.stop_reason = "cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛给事件循环
        logger.exception("run %s 意外失败", state.run_id)
        state.status = RunStatus.failed
        state.error = f"{exc.__class__.__name__}: {exc}"
    finally:
        state.finished_at = state.finished_at or datetime.now()
        state.done.set()
        _broadcast(state, None)  # 关闭信号


def _record(state: RunState, event: Event) -> None:
    """记一份状态快照 + 存进环形缓冲 + 广播。"""
    payload = event.payload

    if event.type is EventType.status:
        try:
            state.status = RunStatus(payload.get("status", state.status.value))
        except ValueError:
            pass
    elif event.type is EventType.plan:
        state.plan = payload
    elif event.type is EventType.search_results:
        # search_results 只报「这次新增了几条」，累计值要自己加；
        # 每轮末尾的 round_end 会带权威总数覆盖一遍，误差不会累积
        state.source_count += payload.get("added", 0)
    elif event.type is EventType.round_end:
        state.round = event.round
        state.source_count = payload.get("total_sources", state.source_count)
        state.note_count = payload.get("total_notes", state.note_count)
    elif event.type is EventType.stop:
        state.stop_reason = payload.get("reason")
    elif event.type is EventType.done:
        state.status = RunStatus.done
        state.stop_reason = payload.get("stop_reason") or state.stop_reason
        state.source_count = payload.get("sources", state.source_count)
        state.note_count = payload.get("notes", state.note_count)
        state.report = payload.get("report")
    elif event.type is EventType.error and payload.get("fatal"):
        state.status = RunStatus.failed
        state.error = payload.get("message")
    elif event.type is EventType.cancelled:
        state.status = RunStatus.cancelled
        state.stop_reason = "cancelled"

    state.events.append(event)
    _broadcast(state, event)


def _broadcast(state: RunState, event: Event | None) -> None:
    for queue in list(state.subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - 队列无上限，理论不可达
            logger.warning("订阅者队列已满，丢弃事件 seq=%s", event.seq if event else None)


# ══════════════════════════════════════════════════════════════
# 路由
# ══════════════════════════════════════════════════════════════

registry = RunRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 - FastAPI 的签名要求
    yield
    await registry.shutdown()


app = FastAPI(
    title="深度研究助手",
    description="给定研究主题，自动规划 / 多轮检索 / 阅读抽取 / 缺口补检 / 综合出带来源的报告",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/research", status_code=202)
async def create_research(request: ResearchRequest) -> dict[str, Any]:
    """建一个研究任务并**立即返回** —— 编排在后台跑，进度从 events 订阅。"""
    run_id = uuid_run_id()
    state = registry.create(request, run_id)
    registry.start(state)
    return {
        "run_id": run_id,
        "status": state.status.value,
        "events": f"/api/research/{run_id}/events",
    }


@app.get("/api/research/{run_id}")
async def get_research(run_id: str) -> dict[str, Any]:
    return registry.get(run_id).snapshot()


@app.get("/api/research/{run_id}/report")
async def get_report(run_id: str) -> dict[str, Any]:
    state = registry.get(run_id)
    if state.report is None:
        raise HTTPException(
            status_code=409,
            detail=f"研究尚未完成（当前状态 {state.status.value}），请先订阅事件流",
        )
    return state.report


@app.post("/api/research/{run_id}/cancel")
async def cancel_research(run_id: str) -> dict[str, Any]:
    state = registry.get(run_id)
    if state.task is not None and not state.task.done():
        state.task.cancel()
        return {"run_id": run_id, "status": "cancelling"}
    return {"run_id": run_id, "status": state.status.value}


@app.get("/api/research/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    since: int = Query(default=0, ge=0, description="从哪个 seq 之后开始（断线重连用）"),
) -> StreamingResponse:
    """SSE 事件流。断线重连带 ``Last-Event-ID`` 头或 ``?since=N`` 都能续上。"""
    state = registry.get(run_id)

    # 两个来源取大的：浏览器重连自动带 Last-Event-ID，curl 调试时用 ?since=
    resume_after = max(_parse_seq(last_event_id), since)

    return StreamingResponse(
        _event_stream(state, request, resume_after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 默认会把 SSE 缓冲起来，明确关掉
        },
    )


async def _event_stream(state: RunState, request: Request, resume_after: int):
    """重放缓冲 → 转直播。"""
    queue: asyncio.Queue[Event | None] = asyncio.Queue()
    # **先订阅再重放**：反过来的话，这两步之间到达的事件会永久丢失
    state.subscribers.add(queue)
    last_seq = resume_after

    try:
        yield make_event(
            EventType.hello,
            run_id=state.run_id,
            seq=0,
            status=state.status.value,
            last_seq=state.events[-1].seq if state.events else 0,
        ).to_sse()

        for event in list(state.events):
            if event.seq <= resume_after:
                continue
            # 纯进度事件不值得在重连时重放一遍，它们的价值在于「当时看到」
            if event.type in TRANSIENT_TYPES:
                continue
            yield event.to_sse()
            last_seq = event.seq

        if state.done.is_set() and queue.empty():
            return

        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL_S)
            except TimeoutError:
                yield ": ping\n\n"  # 注释帧，只为让连接别被中间层掐掉
                continue

            if event is None:
                return
            # 重放与订阅之间必然有重叠窗口，靠 seq 去重
            if event.seq <= last_seq:
                continue
            yield event.to_sse()
            last_seq = event.seq
    finally:
        state.subscribers.discard(queue)


def _parse_seq(value: str | None) -> int:
    try:
        return max(0, int(value)) if value else 0
    except (TypeError, ValueError):
        return 0


def uuid_run_id() -> str:
    return uuid.uuid4().hex[:12]


__all__ = ["app", "registry"]
