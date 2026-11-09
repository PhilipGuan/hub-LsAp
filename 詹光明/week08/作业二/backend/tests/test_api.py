"""FastAPI + SSE 接口层。

阶段 6 的验收：``curl -N .../events`` 看到有序事件流逐个吐出，``?since=N`` 能精确续上。

这里分两层验：

* **路由层** —— 用 ``TestClient`` 走真实 HTTP，确认创建/查询/报告/取消的契约，
  以及「创建立即返回、编排在后台跑」这条关键设计真的成立。
* **事件流层** —— 直接驱动 ``_event_stream``。SSE 的实时性、重放、去重、心跳
  都在这个生成器里，直接驱动它比隔着 HTTP 断言精确得多（也快得多）。

编排用的 runner 可替换，所以整份测试不联网。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app import api
from app.api import RunState, _event_stream, _parse_seq, app
from app.events import Event, EventType, make_event
from app.models import ResearchRequest, RunStatus

# ══════════════════════════════════════════════════════════════
# 测试替身
# ══════════════════════════════════════════════════════════════


def scripted_runner(events: list[Event]):
    """把预设事件原样吐出来的 runner。"""

    async def runner(request: ResearchRequest, run_id: str) -> AsyncIterator[Event]:
        for event in events:
            yield event

    return runner


def happy_events(run_id: str = "run") -> list[Event]:
    """一次最小但完整的成功研究。"""
    report = {
        "topic": "测试主题",
        "title": "测试主题研究报告",
        "executive_summary": "摘要",
        "sections": [],
        "key_conclusions": [],
        "open_questions": [],
        "sources": [],
        "confidence": {"overall_score": 0.5, "level": "medium"},
        "process": {},
    }
    return [
        make_event(EventType.status, run_id=run_id, seq=1, status="planning"),
        make_event(EventType.plan, run_id=run_id, seq=2, sub_questions=[], queries=["q1"]),
        make_event(
            EventType.round_end,
            run_id=run_id,
            seq=3,
            round_no=1,
            total_sources=3,
            total_notes=2,
        ),
        make_event(EventType.confidence, run_id=run_id, seq=4, score=0.5, level="medium"),
        make_event(EventType.report, run_id=run_id, seq=5, markdown="# 报告"),
        make_event(EventType.stop, run_id=run_id, seq=6, reason="sufficient"),
        make_event(
            EventType.done,
            run_id=run_id,
            seq=7,
            round_no=1,
            status="done",
            sources=3,
            notes=2,
            stop_reason="sufficient",
            report=report,
        ),
    ]


def blocking_runner(started: asyncio.Event):
    """起手先发一个事件，然后挂住不结束 —— 用来观察「跑到一半」的接口状态。"""

    async def runner(request: ResearchRequest, run_id: str) -> AsyncIterator[Event]:
        yield make_event(EventType.status, run_id=run_id, seq=1, status="searching")
        started.set()
        await asyncio.sleep(30)

    return runner


class FakeRequest:
    """``_event_stream`` 只用到 ``is_disconnected()``，鸭子类型就够了。"""

    def __init__(self, disconnected: bool = False) -> None:
        self._disconnected = disconnected
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self._disconnected


def make_state(events: list[Event] | None = None, *, done: bool = False) -> RunState:
    state = RunState(run_id="r1", request=ResearchRequest(topic="测试主题"))
    for event in events or []:
        state.events.append(event)
    if done:
        state.done.set()
    return state


def parse_frames(text: str) -> list[dict]:
    """把 SSE 文本切成帧。``: ping`` 注释帧单独标出来。"""
    frames: list[dict] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith(":"):
            frames.append({"comment": block})
            continue
        frame: dict = {}
        for line in block.splitlines():
            key, _, value = line.partition(": ")
            frame[key] = value
        if "data" in frame:
            frame["json"] = json.loads(frame["data"])
        frames.append(frame)
    return frames


@pytest.fixture(autouse=True)
def _isolate_registry():
    """registry 是模块级单例 —— 每个用例前后都得清干净，否则互相串。"""
    api.registry.reset()
    yield
    api.registry.reset()


@pytest.fixture
def client():
    """**必须用 with**：它进入 lifespan 并全程复用同一个事件循环。

    不这么写的话 TestClient 每次请求都新开一个 portal，``create_task`` 起的
    后台任务会随着那次请求的循环一起被销毁 —— 于是「创建后还能查到进度」
    这条最关键的契约根本测不出来（会看到任务莫名其妙已经 cancelled）。
    """
    with TestClient(app) as test_client:
        yield test_client


# ══════════════════════════════════════════════════════════════
# POST /api/research
# ══════════════════════════════════════════════════════════════


class TestCreate:
    def test_returns_202_with_run_id_immediately(self, client) -> None:
        api.registry.runner = scripted_runner([])

        response = client.post("/api/research", json={"topic": "测试主题"})

        assert response.status_code == 202
        assert response.json()["run_id"]
        assert response.json()["events"].endswith("/events")

    def test_topic_too_short_rejected(self, client) -> None:
        response = client.post("/api/research", json={"topic": "短"})

        assert response.status_code == 422

    def test_max_rounds_out_of_range_rejected(self, client) -> None:
        response = client.post("/api/research", json={"topic": "测试主题", "max_rounds": 99})

        assert response.status_code == 422

    def test_编排在后台跑_请求立即返回(self, client) -> None:
        """**接口不能等研究跑完才响应** —— 研究要几分钟，那样必然超时。"""
        started = asyncio.Event()
        api.registry.runner = blocking_runner(started)

        response = client.post("/api/research", json={"topic": "测试主题"})

        assert response.status_code == 202  # 编排还挂在那里，响应已经回来了


# ══════════════════════════════════════════════════════════════
# GET /api/research/{id}
# ══════════════════════════════════════════════════════════════


class TestProgress:
    def test_unknown_run_404(self, client) -> None:
        assert client.get("/api/research/deadbeef").status_code == 404

    def test_snapshot_after_completion(self, client) -> None:
        api.registry.runner = scripted_runner(happy_events())

        run_id = client.post("/api/research", json={"topic": "测试主题"}).json()["run_id"]
        snapshot = _poll_until_done(client, run_id)

        assert snapshot["status"] == "done"
        assert snapshot["sources"] == 3
        assert snapshot["notes"] == 2
        assert snapshot["stop_reason"] == "sufficient"
        assert snapshot["report_ready"] is True
        assert snapshot["max_rounds"] == 3

    def test_snapshot_echoes_topic_and_last_seq(self, client) -> None:
        api.registry.runner = scripted_runner(happy_events())

        run_id = client.post("/api/research", json={"topic": "测试主题"}).json()["run_id"]
        snapshot = _poll_until_done(client, run_id)

        assert snapshot["topic"] == "测试主题"
        assert snapshot["last_seq"] == 7


# ══════════════════════════════════════════════════════════════
# GET /api/research/{id}/report
# ══════════════════════════════════════════════════════════════


class TestReport:
    def test_409_while_still_running(self, client) -> None:
        """报告没好时给 409 而不是空对象 —— 空报告和「还没跑完」是两回事。"""
        started = asyncio.Event()
        api.registry.runner = blocking_runner(started)

        run_id = client.post("/api/research", json={"topic": "测试主题"}).json()["run_id"]

        response = client.get(f"/api/research/{run_id}/report")

        assert response.status_code == 409
        assert "尚未完成" in response.json()["detail"]

    def test_returns_report_after_done(self, client) -> None:
        api.registry.runner = scripted_runner(happy_events())

        run_id = client.post("/api/research", json={"topic": "测试主题"}).json()["run_id"]
        _poll_until_done(client, run_id)
        report = client.get(f"/api/research/{run_id}/report").json()

        assert report["title"] == "测试主题研究报告"
        assert report["confidence"]["level"] == "medium"

    def test_404_for_unknown_run(self, client) -> None:
        assert client.get("/api/research/deadbeef/report").status_code == 404


# ══════════════════════════════════════════════════════════════
# POST /api/research/{id}/cancel
# ══════════════════════════════════════════════════════════════


class TestCancel:
    def test_cancel_stops_a_running_research(self, client) -> None:
        started = asyncio.Event()
        api.registry.runner = blocking_runner(started)

        run_id = client.post("/api/research", json={"topic": "测试主题"}).json()["run_id"]
        assert client.post(f"/api/research/{run_id}/cancel").json()["status"] == "cancelling"

        snapshot = _poll_until(client, run_id, {"cancelled"})
        assert snapshot["status"] == "cancelled"
        # 结束原因不能留空 —— 前端要拿它解释「为什么停了」
        assert snapshot["stop_reason"] == "cancelled"

    def test_cancel_finished_run_is_a_noop(self, client) -> None:
        api.registry.runner = scripted_runner(happy_events())

        run_id = client.post("/api/research", json={"topic": "测试主题"}).json()["run_id"]
        _poll_until_done(client, run_id)

        assert client.post(f"/api/research/{run_id}/cancel").json()["status"] == "done"


# ══════════════════════════════════════════════════════════════
# 状态快照的记账（纯函数，不经过 HTTP）
# ══════════════════════════════════════════════════════════════


class TestRecord:
    def test_search_results_accumulate_but_round_end_wins(self) -> None:
        """search_results 只报增量；round_end 带的是权威总数，后者必须覆盖前者。"""
        state = make_state()
        api._record(state, make_event(EventType.search_results, seq=1, added=3))
        assert state.source_count == 3
        api._record(state, make_event(EventType.search_results, seq=2, added=2))
        assert state.source_count == 5

        api._record(state, make_event(EventType.round_end, seq=3, total_sources=4, total_notes=1))
        assert state.source_count == 4
        assert state.note_count == 1

    def test_non_fatal_error_does_not_fail_the_run(self) -> None:
        """单次搜索 429 只是发个事件 —— 挂了整个研究才是更差的体验。"""
        state = make_state()
        api._record(state, make_event(EventType.error, seq=1, scope="search", fatal=False))

        assert state.status is not RunStatus.failed
        assert state.error is None

    def test_fatal_error_marks_run_failed(self) -> None:
        state = make_state()
        api._record(state, make_event(EventType.error, seq=1, fatal=True, message="planner 崩了"))

        assert state.status is RunStatus.failed
        assert state.error == "planner 崩了"

    def test_cancelled_event_updates_status_and_reason(self) -> None:
        state = make_state()
        api._record(state, make_event(EventType.cancelled, seq=1))

        assert state.status is RunStatus.cancelled
        assert state.stop_reason == "cancelled"

    def test_cancel_overwrites_a_stale_stop_reason(self) -> None:
        """跑到一半被掐断时，别把上一轮反思写的 sufficient 留在快照里。"""
        state = make_state()
        api._record(state, make_event(EventType.stop, seq=1, reason="sufficient"))

        api._record(state, make_event(EventType.cancelled, seq=2))

        assert state.stop_reason == "cancelled"

    def test_done_event_captures_report_and_counts(self) -> None:
        state = make_state()
        api._record(
            state,
            make_event(
                EventType.done,
                seq=1,
                status="done",
                sources=7,
                notes=4,
                stop_reason="max_rounds_reached",
                report={"title": "报告"},
            ),
        )

        assert state.report == {"title": "报告"}
        assert state.source_count == 7
        assert state.stop_reason == "max_rounds_reached"

    def test_unknown_status_string_does_not_crash(self) -> None:
        """引擎将来加了新状态而这里没跟上时，不该把整个 run 带崩。"""
        state = make_state()
        api._record(state, make_event(EventType.status, seq=1, status="brand_new_state"))

        assert state.status is RunStatus.init


# ══════════════════════════════════════════════════════════════
# 事件流（直接驱动生成器）
# ══════════════════════════════════════════════════════════════


class TestStreamReplay:
    async def test_finished_run_replays_and_closes(self) -> None:
        state = make_state(happy_events(), done=True)

        frames = [frame async for frame in _event_stream(state, FakeRequest(), 0)]

        assert "event: hello" in frames[0]
        # hello + 6 个事件（7 个里有一个是 status，重放时跳过）
        assert len(frames) == 7
        assert "event: done" in frames[-1]

    async def test_since_skips_already_seen(self) -> None:
        """断线重连：``?since=5`` 之后只该看到 6、7。"""
        state = make_state(happy_events(), done=True)

        frames = [frame async for frame in _event_stream(state, FakeRequest(), 5)]
        seqs = [parse_frames(frame)[0]["id"] for frame in frames[1:]]

        assert seqs == ["6", "7"], "since 之后的事件才该出现，且一条不多一条不少"

    async def test_since_beyond_end_replays_nothing(self) -> None:
        state = make_state(happy_events(), done=True)

        frames = [frame async for frame in _event_stream(state, FakeRequest(), 99)]

        assert len(frames) == 1  # 只有 hello

    async def test_transient_events_skipped_on_replay(self) -> None:
        """status 只影响进度显示 —— 重连时重放它没有意义，还会把进度条拽回去。"""
        state = make_state(happy_events(), done=True)

        frames = [frame async for frame in _event_stream(state, FakeRequest(), 0)]

        assert not any("event: status" in frame for frame in frames)
        assert any("event: plan" in frame for frame in frames)

    async def test_truncated_buffer_still_streams(self) -> None:
        """环形缓冲被挤掉早期事件时，剩下的仍然照常重放。"""
        events = happy_events()[3:]
        state = make_state(events, done=True)

        frames = [frame async for frame in _event_stream(state, FakeRequest(), 0)]

        assert parse_frames(frames[1])[0]["id"] == "4"


class TestStreamLive:
    async def test_live_events_arrive_after_replay(self) -> None:
        state = make_state()
        stream = _event_stream(state, FakeRequest(), 0)

        assert "event: hello" in await stream.__anext__()

        api._record(state, make_event(EventType.search, seq=1, query="q1"))
        frame = await asyncio.wait_for(stream.__anext__(), timeout=5)

        assert "event: search" in frame
        assert parse_frames(frame)[0]["json"]["payload"]["query"] == "q1"

        api._broadcast(state, None)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(stream.__anext__(), timeout=5)

    async def test_overlap_between_replay_and_subscribe_is_deduped(self) -> None:
        """先订阅再重放，两者之间必然重叠 —— 靠 seq 去重，不能重复推。

        造一个真实的乱序：订阅已建立、缓冲还没重放时，同一条消息既在缓冲里
        又在队列里。客户端只该看到它一次。
        """
        state = make_state(happy_events()[:3])
        stream = _event_stream(state, FakeRequest(), 0)

        # hello 之后订阅已经建立，此时缓冲里 seq 1~3 还没重放
        assert "event: hello" in await stream.__anext__()
        queue = next(iter(state.subscribers))
        queue.put_nowait(make_event(EventType.plan, seq=3, sub_questions=[]))  # 与缓冲重复
        queue.put_nowait(make_event(EventType.reflect, seq=4, coverage={}))  # 真正的新事件

        frames = [await asyncio.wait_for(stream.__anext__(), timeout=5) for _ in range(3)]

        # 1 是 transient 跳过；3 来自缓冲，队列里那条重复的被丢掉；然后是 4
        assert [parse_frames(f)[0]["id"] for f in frames] == ["2", "3", "4"]

        api._broadcast(state, None)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(stream.__anext__(), timeout=5)

    async def test_finished_run_does_not_hang_the_connection(self) -> None:
        """已经跑完的 run，订阅后重放完立刻收尾 —— 不能让客户端一直挂着。"""
        state = make_state(happy_events(), done=True)

        frames = await asyncio.wait_for(_collect(_event_stream(state, FakeRequest(), 0)), timeout=2)

        assert "event: done" in frames[-1]

    async def test_disconnect_stops_the_stream(self) -> None:
        state = make_state()
        request = FakeRequest(disconnected=True)
        stream = _event_stream(state, request, 0)

        await stream.__anext__()  # hello
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(stream.__anext__(), timeout=2)

    async def test_subscriber_unregistered_on_exit(self) -> None:
        """退订必须发生在 finally 里 —— 否则订阅者集合会随断线次数无限涨。"""
        state = make_state(happy_events(), done=True)

        await _collect(_event_stream(state, FakeRequest(), 0))

        assert state.subscribers == set()


class TestStreamFraming:
    async def test_frame_has_id_event_and_json_data(self) -> None:
        state = make_state(happy_events(), done=True)

        frames = [frame async for frame in _event_stream(state, FakeRequest(), 0)]
        first = parse_frames(frames[1])[0]

        assert first["id"] == "2"
        assert first["event"] == "plan"
        assert first["json"]["run_id"] == "run"
        assert first["json"]["seq"] == 2
        assert "ts" in first["json"]


class TestStreamRoute:
    """路由是否真的接上了 ``_event_stream`` —— 上面那些用例绕过了 HTTP。"""

    def test_stream_endpoint_emits_sse_frames(self, client) -> None:
        api.registry.runner = scripted_runner(happy_events("run"))

        run_id = client.post("/api/research", json={"topic": "测试主题"}).json()["run_id"]
        _poll_until_done(client, run_id)

        with client.stream("GET", f"/api/research/{run_id}/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            text = "".join(response.iter_text())

        frames = parse_frames(text)
        types = [frame.get("event") for frame in frames]

        assert types[0] == "hello"
        assert "plan" in types
        assert types[-1] == "done"

    def test_since_query_param_replays_the_tail(self, client) -> None:
        api.registry.runner = scripted_runner(happy_events("run"))

        run_id = client.post("/api/research", json={"topic": "测试主题"}).json()["run_id"]
        _poll_until_done(client, run_id)

        with client.stream("GET", f"/api/research/{run_id}/events?since=5") as response:
            text = "".join(response.iter_text())

        ids = [frame["id"] for frame in parse_frames(text) if "id" in frame]

        assert ids == ["0", "6", "7"]  # 0 是 hello 帧

    def test_last_event_id_header_wins_when_larger(self, client) -> None:
        """浏览器重连自动带这个头，它的进度比 URL 里的 since 更新。"""
        api.registry.runner = scripted_runner(happy_events("run"))

        run_id = client.post("/api/research", json={"topic": "测试主题"}).json()["run_id"]
        _poll_until_done(client, run_id)

        with client.stream(
            "GET",
            f"/api/research/{run_id}/events?since=1",
            headers={"Last-Event-ID": "6"},
        ) as response:
            text = "".join(response.iter_text())

        ids = [frame["id"] for frame in parse_frames(text) if "id" in frame]

        assert ids == ["0", "7"]

    def test_stream_404_for_unknown_run(self, client) -> None:
        assert client.get("/api/research/deadbeef/events").status_code == 404


class TestCancelViaStream:
    async def test_cancelled_event_reaches_subscribers(self) -> None:
        state = make_state()
        stream = _event_stream(state, FakeRequest(), 0)
        await stream.__anext__()  # hello

        api._record(state, make_event(EventType.cancelled, seq=1))
        frame = await asyncio.wait_for(stream.__anext__(), timeout=5)

        assert "event: cancelled" in frame


# ══════════════════════════════════════════════════════════════
# registry 自身
# ══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_eviction_keeps_the_newest(self) -> None:
        """内存缓冲不落盘，就必须有上限 —— 否则长时间跑会把内存吃光。"""
        registry = api.RunRegistry()
        request = ResearchRequest(topic="测试主题")

        for i in range(api.MAX_RUNS + 5):
            state = registry.create(request, f"run{i}")
            state.done.set()

        assert len(registry._runs) <= api.MAX_RUNS + 1
        assert "run0" not in registry._runs
        assert f"run{api.MAX_RUNS + 4}" in registry._runs

    def test_eviction_never_drops_a_running_run(self) -> None:
        registry = api.RunRegistry()
        request = ResearchRequest(topic="测试主题")
        for i in range(api.MAX_RUNS):
            registry.create(request, f"done{i}").done.set()
        running = registry.create(request, "running")

        assert registry._runs["running"] is running

    async def test_shutdown_cancels_running_tasks(self) -> None:
        registry = api.RunRegistry(blocking_runner(asyncio.Event()))
        state = registry.create(ResearchRequest(topic="测试主题"), "r1")
        registry.start(state)

        await registry.shutdown()

        assert state.task is not None and state.task.cancelled()

    def test_parse_seq_tolerates_junk(self) -> None:
        assert _parse_seq(None) == 0
        assert _parse_seq("") == 0
        assert _parse_seq("abc") == 0
        assert _parse_seq("-5") == 0
        assert _parse_seq("12") == 12


# ══════════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════════


async def _collect(agen) -> list[str]:
    return [frame async for frame in agen]


def _poll_until_done(client: TestClient, run_id: str, timeout_s: float = 3.0) -> dict:
    return _poll_until(client, run_id, {"done"}, timeout_s)


def _poll_until(client: TestClient, run_id: str, wanted: set[str], timeout_s: float = 3.0) -> dict:
    """后台任务在另一个事件循环里跑，TestClient 是同步的 —— 只能轮询等它落地。"""
    import time

    deadline = time.monotonic() + timeout_s
    snapshot = client.get(f"/api/research/{run_id}").json()
    while snapshot["status"] not in wanted and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = client.get(f"/api/research/{run_id}").json()
    return snapshot
