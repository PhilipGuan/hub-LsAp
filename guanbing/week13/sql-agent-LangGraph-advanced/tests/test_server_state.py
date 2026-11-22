"""批 3 —— 状态与准入（`PRODUCTION.md` §4）。

四条验收：

1. **重启进程后旧 `session_id` 仍可续问**（检查点 + 会话元数据落到同一个 SQLite 文件，D8）；
2. 口令错误 → 401（D10）；
3. 配额用尽 → 提示话术（D11）；
4. 审计表有本轮记录（D14）。

这里的「重启进程」是**真的重启**：连开两个 `TestClient` 上下文（各自跑一遍 lifespan，
退出时会清空 `_state` / `_sessions`），两者之间只有 `STATE_DB_PATH` 指向的 SQLite 文件是共享的。

除此之外全部**离线**：图路线的 `sqlagent.llm` 两个入口换成假实现，边界层（store / 口令 /
配额 / 审计 / 检查点）保持真实。`patch_llm` 与 `test_server_api.py` 同款——夹具不跨模块共享，
这里是刻意的小重复。
"""

from __future__ import annotations

import sqlite3
from itertools import chain

import pytest
from fastapi.testclient import TestClient

import sqlagent
import server.app as server_app
from server import store as store_module
from sqlagent import llm
from sqlagent.schemas import ScopeJudgement, TableSelection

QUESTION = "数据库里一共有多少首歌曲？"
GOOD_SQL = "SELECT COUNT(*) FROM tracks;"
ANSWER = "共有 3503 首歌曲。"
IN_SCOPE = ScopeJudgement(scope="in", reason="统计歌曲", tables=["tracks"])


@pytest.fixture
def state_db(tmp_path):
    """每个用例一个独立的状态库文件。"""
    return tmp_path / "state.sqlite"


@pytest.fixture
def open_client(state_db, monkeypatch):
    """返回「开一个 TestClient 上下文」的工厂；`with` 进出 = 一次进程起停。"""
    monkeypatch.setenv("STATE_DB_PATH", str(state_db))
    return lambda: TestClient(server_app.app, raise_server_exceptions=False)


@pytest.fixture
def client(open_client):
    with open_client() as c:
        yield c


@pytest.fixture
def patch_llm(monkeypatch):
    """安装假 LLM：`scope` 可给单个或一串（按调用顺序消费），`texts` 按顺序消费。"""

    def install(*, scope, tables=("tracks",), texts):
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


def _new(client: TestClient, *, route: str = "graph", human_review: bool = False) -> str:
    r = client.post("/api/session", json={"route": route, "human_review": human_review})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _ask(client: TestClient, sid: str, message: str) -> dict:
    r = client.post("/api/chat", json={"session_id": sid, "message": message})
    assert r.status_code == 200, r.text
    return r.json()


def _audit_rows(state_db) -> list[tuple]:
    conn = sqlite3.connect(state_db)
    try:
        return conn.execute(
            "SELECT trace_id, session_id, route, status, degraded, elapsed_ms, sql, rows"
            " FROM audit ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 验收 1：重启进程后旧 session_id 仍可续问（D8）
# --------------------------------------------------------------------------- #

def test_session_survives_process_restart(open_client, patch_llm):
    patch_llm(
        scope=[IN_SCOPE, IN_SCOPE],
        # 第 1 轮：生成 SQL + 组织回答；第 2 轮：追问改写 + 生成 SQL + 组织回答
        texts=[GOOD_SQL, ANSWER, QUESTION, GOOD_SQL, ANSWER],
    )

    with open_client() as c1:
        sid = _new(c1)
        assert _ask(c1, sid, QUESTION)["trace"]["rows"] == [[3503]]

    # 进程退出：内存态清空（`_sessions` / `_state`），只剩 SQLite 文件
    assert server_app._sessions == {}
    assert server_app._state == {}

    with open_client() as c2:
        # 同一个 `session_id` 直接续问 —— 会话元数据 + 图状态都从文件里重建
        env = _ask(c2, sid, QUESTION)
        assert env["session_id"] == sid
        assert env["route"] == "graph"
        assert env["status"] == "done"
        assert env["trace"]["rows"] == [[3503]]

        # 上一轮的历史也还在（`turns` 读自检查点，不依赖进程内存）
        detail = c2.get(f"/api/session/{sid}").json()
        assert detail["turns"][0][0] == QUESTION
        assert detail["trajectory"]


def test_unknown_session_still_404_after_restart(open_client):
    with open_client() as c:
        assert c.post("/api/chat", json={"session_id": "查无此会", "message": "hi"}).status_code == 404


# --------------------------------------------------------------------------- #
# 验收 2：口令（D10）
# --------------------------------------------------------------------------- #

def test_passcode_gate(open_client, patch_llm, monkeypatch):
    monkeypatch.setenv("APP_PASSCODE", "s3cret")
    patch_llm(scope=IN_SCOPE, texts=[GOOD_SQL, ANSWER])

    with open_client() as c:
        # 口令页本身要能打开（`GET /` 不设闸门），否则用户无从输入
        assert c.get("/").status_code == 200
        assert c.get("/api/auth").json() == {"required": True, "ok": False}

        # 受保护接口：没带 cookie 一律 401 + 话术
        r = c.post("/api/session", json={})
        assert r.status_code == 401
        assert "口令" in r.json()["detail"] and r.json()["trace_id"]

        # 口令错 → 401；口令对 → httpOnly cookie
        assert c.post("/api/auth", json={"passcode": "wrong"}).status_code == 401
        r = c.post("/api/auth", json={"passcode": "s3cret"})
        assert r.status_code == 200 and r.json() == {"ok": True, "required": True}
        assert "httponly" in r.headers["set-cookie"].lower()
        assert c.get("/api/auth").json()["ok"] is True

        # 之后的请求由 TestClient 自动带 cookie → 正常跑通
        sid = _new(c)
        assert _ask(c, sid, QUESTION)["status"] == "done"


def test_passcode_off_by_default(open_client):
    with open_client() as c:
        assert c.get("/api/auth").json() == {"required": False, "ok": True}
        assert c.post("/api/session", json={}).status_code == 200


def test_passcode_read_from_env(monkeypatch):
    monkeypatch.setenv("APP_PASSCODE", "  abc  ")
    assert server_app._passcode() == "abc"      # 首尾空格忽略
    monkeypatch.setenv("APP_PASSCODE", "")
    assert server_app._passcode() == ""         # 空 = 闸门关闭


# --------------------------------------------------------------------------- #
# 验收 3：配额（D11）
# --------------------------------------------------------------------------- #

def test_session_quota_exhausted(open_client, patch_llm, monkeypatch):
    monkeypatch.setattr(store_module, "SESSION_ROUNDS", 1)
    patch_llm(scope=IN_SCOPE, texts=[GOOD_SQL, ANSWER])

    with open_client() as c:
        sid = _new(c)
        assert _ask(c, sid, QUESTION)["status"] == "done"

        r = c.post("/api/chat", json={"session_id": sid, "message": "再问一次"})
        assert r.status_code == 429
        assert "新建会话" in r.json()["detail"] and r.json()["trace_id"]


def test_global_quota_exhausted(open_client, patch_llm, monkeypatch):
    monkeypatch.setattr(store_module, "GLOBAL_DAILY_ROUNDS", 1)
    patch_llm(scope=IN_SCOPE, texts=[GOOD_SQL, ANSWER])

    with open_client() as c:
        assert _ask(c, _new(c), QUESTION)["status"] == "done"

        # 换个会话也拦得住：全局额度是**跨会话**的
        r = c.post("/api/chat", json={"session_id": _new(c), "message": QUESTION})
        assert r.status_code == 429
        assert "全站" in r.json()["detail"]


def test_quota_not_burned_by_unknown_session(open_client, patch_llm, monkeypatch):
    """先 404 后配额：未知会话不该消耗额度（否则撞库名就能把额度烧光）。"""
    monkeypatch.setattr(store_module, "GLOBAL_DAILY_ROUNDS", 1)
    patch_llm(scope=IN_SCOPE, texts=[GOOD_SQL, ANSWER])

    with open_client() as c:
        assert c.post("/api/chat",
                      json={"session_id": "查无此会", "message": QUESTION}).status_code == 404
        assert _ask(c, _new(c), QUESTION)["status"] == "done"


# --------------------------------------------------------------------------- #
# 验收 4：审计表（D14）
# --------------------------------------------------------------------------- #

def test_audit_row_recorded_per_round(client, patch_llm, state_db):
    patch_llm(scope=IN_SCOPE, texts=[GOOD_SQL, ANSWER])
    sid = _new(client)

    env = _ask(client, sid, QUESTION)

    rows = _audit_rows(state_db)
    assert len(rows) == 1
    trace_id, session_id, route, status, degraded, elapsed_ms, sql, row_count = rows[0]
    assert trace_id == env["trace_id"]        # 与响应头 / envelope 里的 trace_id 对得上
    assert (session_id, route) == (sid, "graph")
    assert status == "done" and degraded == 0
    assert sql == GOOD_SQL and row_count == 1
    assert elapsed_ms > 0


# --------------------------------------------------------------------------- #
# 会话保留期（D8）：7 天未访问即清理
# --------------------------------------------------------------------------- #

def test_store_prunes_sessions_older_than_ttl(tmp_path):
    st = store_module.Store(store_module.connect(tmp_path / "state.sqlite"))
    try:
        st.create_session("old", "t-old", "graph", False,
                          created_at=store_module._now(-8))
        st.create_session("new", "t-new", "graph", True)

        assert st.get_session("old") is None                    # 过期即「不存在」
        assert st.get_session("new")["human_review"] is True

        assert st.prune_expired() == ["t-old"]                  # 报出要连带清理的 thread_id
        assert [r[0] for r in st.conn.execute("SELECT session_id FROM sessions")] == ["new"]
    finally:
        st.close()


# --------------------------------------------------------------------------- #
# 批 4（D16）：`/health` 增强 —— 版本 + 状态库可写性
# --------------------------------------------------------------------------- #

def test_health_reports_version_and_writable_state_db(client):
    body = client.get("/health").json()
    assert {"ok", "version", "tables", "state_db_writable"} <= set(body)
    assert body["ok"] is True
    assert body["version"] == sqlagent.__version__
    assert body["state_db_writable"] is True
    assert body["tables"] > 0                    # chinook 反射出的表数（当前 11）


def test_probe_writable_detects_readonly_state_db(tmp_path):
    """状态库/挂卷只读时要能被探测到 —— 这正是不该等到用户提问才炸的那种事故。"""
    path = tmp_path / "state.sqlite"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE a(x)")
    raw.commit()
    raw.close()
    path.chmod(0o444)                            # 只读文件 + 新开的连接

    conn = sqlite3.connect(path)
    try:
        assert store_module.probe_writable(conn) is False
    finally:
        conn.close()
