"""server 边界层测试：单一入口自动分流（D5）+ 信封（D15）+ 异常兜底（D6）。

全部**离线**（零网络）：沿用 `tests/conftest.py` 与 `tests/test_agent_route.py` 的手法——
图路线的 `sqlagent.llm` 两个入口换成假实现；agent 路线的模型换成脚本假模型。
被测试的边界层（路由 / 口令壳 / 日志 / envelope / 异常兜底）**保持真实**。

为什么值得单独测这一层：`DESIGN.md` §4 的四条不变量锁的是 `sqlagent/` 的行为，
而「同一个入口能自动分流五种场景」（D5）是**边界层**的职责，只能在这一层被钉住。
"""

from __future__ import annotations

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import server.app as server_app
import sqlagent.agents.session as agent_session
import sqlagent.agents.sql_agent as sql_agent_module
from sqlagent import llm
from sqlagent.schemas import ScopeJudgement, TableSelection
from tests.test_agent_route import ScriptedModel, sql_call_then_answer

GOOD_SQL = "SELECT COUNT(*) FROM tracks;"
BAD_SQL = "SELECT * FROM 不存在的表;"
ANSWER = "共有 3503 首歌曲。"

# D15 约定的 envelope 键集合（不含 trace 内部的 11 个键——那些逐字由核心库产出）
ENVELOPE_KEYS = {"session_id", "trace_id", "route", "status", "degraded", "events", "trace"}

# graph 路线的 trace 是核心库的 11 键；agent 路线多一个 `route`、少一个 `attempts`
GRAPH_TRACE_KEYS = {"raw_question", "standalone_question", "scope", "tables", "sql", "attempts",
                    "ok", "columns", "rows", "answer", "status"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # 批 3 起状态（检查点 + 会话元数据 + 配额 + 审计）落在一个真实的 SQLite 文件里，
    # 所以这里指到临时目录：既不把产物写进仓库，也让每次跑测试的**配额计数从零开始**
    # （配额是「每日全局 200 轮」，共用仓库目录里那个文件的话会跨次累积）。
    mp = pytest.MonkeyPatch()
    mp.setenv("STATE_DB_PATH", str(tmp_path_factory.mktemp("state") / "state.sqlite"))
    mp.setenv("APP_PASSCODE", "")          # 本文件的用例都跑在「无口令」形态下
    # raise_server_exceptions=False：要能看到全局 Exception 兜底返回的 500（否则 TestClient 直接抛）
    with TestClient(server_app.app, raise_server_exceptions=False) as c:
        yield c
    mp.undo()


@pytest.fixture
def patch_llm(monkeypatch):
    """安装假 LLM：scope 可给单个或一串（按调用顺序消费），tables 固定。"""

    def install(*, scope, tables=("tracks",), texts):
        from itertools import chain

        scopes = list(scope) if isinstance(scope, list) else [scope]
        scope_it = iter(chain(scopes, [None] * 20))

        def structured(schema, messages, **kwargs):
            if schema is ScopeJudgement:
                return next(scope_it)
            if schema is TableSelection:
                return TableSelection(tables=list(tables))
            return None

        text_it = iter(texts)

        def text(messages, **kwargs):
            try:
                return next(text_it)
            except StopIteration:  # pragma: no cover - 仅用于定位测试写错
                raise AssertionError(f"文本调用超出预期（已备 {len(texts)} 条）") from None

        monkeypatch.setattr(llm, "invoke_structured_messages", structured)
        monkeypatch.setattr(llm, "invoke_messages", text)

    return install


@pytest.fixture
def no_fallback(monkeypatch):
    """关掉兜底配置，让 agent 路线的装配不依赖本机 `.env`。"""
    monkeypatch.setattr(sql_agent_module, "get_fallback_settings", lambda *a, **k: None)


@pytest.fixture
def inject_model(monkeypatch):
    """把 `AgentBI` 内部构造的模型换成脚本假模型（middleware / checkpointer 保持真实）。"""
    def install(model: ScriptedModel) -> None:
        real = sql_agent_module.build_autonomous_agent
        monkeypatch.setattr(
            agent_session, "build_autonomous_agent",
            lambda parser, **kw: real(parser, model=model, **kw),
        )
    return install


def _new(client: TestClient, *, route: str = "graph", human_review: bool = False) -> str:
    r = client.post("/api/session", json={"route": route, "human_review": human_review})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _ask(client: TestClient, sid: str, message: str) -> dict:
    r = client.post("/api/chat", json={"session_id": sid, "message": message})
    assert r.status_code == 200, r.text
    return r.json()


def _resume(client: TestClient, sid: str, value) -> dict:
    r = client.post("/api/chat/resume", json={"session_id": sid, "value": value})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# D5 情形一：[in] —— 一路跑通，envelope 形状正确
# --------------------------------------------------------------------------- #

def test_in_branch_envelope(client, patch_llm):
    patch_llm(scope=ScopeJudgement(scope="in", reason="统计歌曲", tables=["tracks"]),
              texts=[GOOD_SQL, ANSWER])
    sid = _new(client)

    r = client.post("/api/chat", json={"session_id": sid, "message": "数据库里一共有多少首歌曲？"})
    assert r.status_code == 200
    env = r.json()

    assert set(env) == ENVELOPE_KEYS
    assert env["session_id"] == sid
    assert env["route"] == "graph"
    assert env["status"] == "done"
    assert env["degraded"] is False
    assert env["events"] == [{"type": "final"}]
    # trace 仍是核心库的 11 键结构（未被打壳改动），rows 经 JSON 序列化后是列表
    assert set(env["trace"]) == GRAPH_TRACE_KEYS
    assert env["trace"]["rows"] == [[3503]]
    assert env["trace"]["sql"] == GOOD_SQL
    # trace_id 同时回写到响应头，用户可复述
    assert r.headers["X-Trace-Id"] == env["trace_id"]


# --------------------------------------------------------------------------- #
# D5 情形二：[out] —— 同一入口直接谢绝，不生成 SQL、不算降级
# --------------------------------------------------------------------------- #

def test_out_of_scope_same_entry(client, patch_llm):
    patch_llm(scope=ScopeJudgement(scope="out", reason="与数据库无关"), texts=[])
    sid = _new(client)

    env = _ask(client, sid, "明天天气怎么样？")

    assert env["status"] == "done"
    assert env["degraded"] is False          # 范围外是业务正常分支，不是失败
    assert env["trace"]["sql"] is None
    assert "超出了当前 chinook 数据库的范围" in env["trace"]["answer"]


# --------------------------------------------------------------------------- #
# D5 情形三：[ambiguous] —— interrupt 反问，resume(字符串) 续接
# --------------------------------------------------------------------------- #

def test_ambiguous_clarify_then_resume(client, patch_llm):
    patch_llm(
        scope=[ScopeJudgement(scope="ambiguous", reason="指代不清", clarify="你问的是哪张表？"),
               ScopeJudgement(scope="in", reason="统计歌曲", tables=["tracks"])],
        texts=[GOOD_SQL, ANSWER],
    )
    sid = _new(client)

    env = _ask(client, sid, "有多少？")
    assert env["status"] == "interrupted"
    assert env["trace"]["status"] == "interrupted"
    assert env["trace"]["interrupt"]["type"] == "clarify"

    env2 = _resume(client, sid, "数据库里一共有多少首歌曲？")
    assert env2["status"] == "done"
    assert env2["trace"]["rows"] == [[3503]]


# --------------------------------------------------------------------------- #
# D5 情形四：[sql_review] —— 同一入口的人工复核闸门（graph 路线）
# --------------------------------------------------------------------------- #

def test_sql_review_approve(client, patch_llm):
    patch_llm(scope=ScopeJudgement(scope="in", reason="ok", tables=["tracks"]),
              texts=[GOOD_SQL, ANSWER])
    sid = _new(client, human_review=True)

    env = _ask(client, sid, "数据库里一共有多少首歌曲？")
    assert env["status"] == "interrupted"
    assert env["trace"]["interrupt"]["type"] == "sql_review"
    assert env["trace"]["interrupt"]["sql"] == GOOD_SQL

    env2 = _resume(client, sid, {"action": "approve"})
    assert env2["status"] == "done"
    assert env2["trace"]["ok"] is True
    assert env2["trace"]["rows"] == [[3503]]


# --------------------------------------------------------------------------- #
# D5 情形五：同一入口 · agent 路线的人工闸门（HumanInTheLoopMiddleware）
# --------------------------------------------------------------------------- #

def test_agent_route_hitl_interrupt_then_approve(client, no_fallback, inject_model):
    inject_model(sql_call_then_answer(GOOD_SQL, ANSWER))
    sid = _new(client, route="agent", human_review=True)

    env = _ask(client, sid, "数据库里一共有多少首歌曲？")
    assert env["route"] == "agent"
    assert env["status"] == "interrupted"
    assert env["trace"]["interrupt"]["action_requests"][0]["name"] == "run_sql"

    env2 = _resume(client, sid, {"decisions": [{"type": "approve"}]})
    assert env2["status"] == "done"
    assert env2["trace"]["answer"] == ANSWER


# --------------------------------------------------------------------------- #
# D6 —— 可预期失败标 degraded
# --------------------------------------------------------------------------- #

def test_degraded_when_sql_never_works(client, patch_llm):
    # 生成 + 3 次改写都给坏 SQL（守卫放行、试跑失败）→ 改写到上限后放弃
    patch_llm(scope=ScopeJudgement(scope="in", reason="ok", tables=["tracks"]),
              texts=[BAD_SQL, BAD_SQL, BAD_SQL, BAD_SQL])
    sid = _new(client)

    env = _ask(client, sid, "数据库里一共有多少首歌曲？")

    assert env["status"] == "degraded"
    assert env["degraded"] is True
    assert env["trace"]["ok"] is False
    assert env["trace"]["rows"] is None


# --------------------------------------------------------------------------- #
# D6 —— 不可预期失败：500 + trace_id + 兜底话术（细节只进日志）
# --------------------------------------------------------------------------- #

def test_unhandled_exception_returns_500_with_trace_id(client, monkeypatch):
    sid = _new(client)   # 建会话本身不需要 LLM

    class Boom:
        def ask(self, *a, **k):
            raise RuntimeError("boom")

        def resume(self, *a, **k):   # pragma: no cover - 本测试用不到
            raise RuntimeError("boom")

    monkeypatch.setitem(server_app._sessions, sid, {"bot": Boom(), "route": "graph"})

    r = client.post("/api/chat", json={"session_id": sid, "message": "hi"})

    assert r.status_code == 500
    body = r.json()
    assert body["trace_id"]
    assert "服务出错了" in body["detail"]
    assert r.headers["X-Trace-Id"] == body["trace_id"]


def test_missing_session_returns_404_with_trace_id(client):
    r = client.post("/api/chat", json={"session_id": "不存在", "message": "hi"})

    assert r.status_code == 404
    body = r.json()
    assert set(body) == {"detail", "trace_id"}
    assert body["trace_id"]
    assert "会话不存在或已过期" in body["detail"]


# --------------------------------------------------------------------------- #
# D11 —— 澄清额度（MAX_CLARIFY）用尽后在同一入口降级收尾
# --------------------------------------------------------------------------- #

def test_clarify_limit_degrades_same_entry(client, patch_llm):
    """连续反问到额度用尽：给出话术收尾、不生成 SQL、也不变成 500。"""
    patch_llm(scope=ScopeJudgement(scope="ambiguous", reason="指代不清", clarify="你问的是哪张表？"),
              texts=[])
    sid = _new(client)

    assert _ask(client, sid, "有多少？")["status"] == "interrupted"
    assert _resume(client, sid, "还是不清楚")["status"] == "interrupted"
    assert _resume(client, sid, "再想想")["status"] == "interrupted"

    env = _resume(client, sid, "我也不知道")

    assert env["status"] == "degraded"
    assert env["degraded"] is True
    assert env["trace"]["sql"] is None
    assert "没能确定" in env["trace"]["answer"]


# --------------------------------------------------------------------------- #
# D6 —— 模型侧不可用（超时 / 429 / 连接失败）降级，而不是 500
# --------------------------------------------------------------------------- #

class _TimeoutBot:
    """只为了让「模型侧抛错」这条路径可复现，不涉及任何真实图。"""

    @staticmethod
    def _boom():
        raise openai.APITimeoutError(
            request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"))

    def ask(self, *a, **k):
        self._boom()

    def resume(self, *a, **k):   # pragma: no cover - 本测试用不到
        self._boom()


def test_llm_unavailable_degrades_not_500(client, monkeypatch):
    """`request_timeout`（D11）落地后最常见的可预期失败：按 D6 降级成话术。"""
    sid = _new(client)
    monkeypatch.setitem(server_app._sessions, sid, {"bot": _TimeoutBot(), "route": "graph"})

    r = client.post("/api/chat", json={"session_id": sid, "message": "有多少首歌？"})

    assert r.status_code == 200                     # 服务本身是好的 → 不返回 500
    env = r.json()
    assert set(env) == ENVELOPE_KEYS
    assert env["status"] == "degraded" and env["degraded"] is True
    # 降级 trace 与核心库同形：前端不必为「降级」再写一套渲染分支
    assert set(env["trace"]) == GRAPH_TRACE_KEYS
    assert env["trace"]["raw_question"] == "有多少首歌？"
    assert env["trace"]["answer"] == server_app.messages.LLM_UNAVAILABLE
    assert env["trace"]["sql"] is None
    assert r.headers["X-Trace-Id"] == env["trace_id"]

    # agent 路线的降级 trace 保持它自己的形状（带 `route`、不带 `attempts`）
    sid2 = _new(client, route="agent")
    monkeypatch.setitem(server_app._sessions, sid2, {"bot": _TimeoutBot(), "route": "agent"})

    env2 = _ask(client, sid2, "有多少首歌？")

    assert env2["status"] == "degraded"
    assert env2["trace"]["route"] == "agent" and "attempts" not in env2["trace"]


def test_agent_route_without_answer_is_degraded(client, no_fallback, inject_model):
    """agent 路线拿不到 `ok`，改用「有没有产出结论」当降级判据。"""
    inject_model(ScriptedModel(script=[AIMessage(content="")]))
    sid = _new(client, route="agent")

    env = _ask(client, sid, "数据库里一共有多少首歌曲？")

    assert env["status"] == "degraded"
    assert env["degraded"] is True
