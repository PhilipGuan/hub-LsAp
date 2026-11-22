"""中英双语（i18n）：文案层 / 边界层话术 / 语言贯通 / 会话语言落库。

双语是**加语言、不加行为**：`lang` 只决定「取哪套文案」，图的拓扑、节点数、LLM 调用次数
一律不变；核心库与 API 的**默认语言仍是 `zh`**，所以既有 111 项测试逐字通过（`DESIGN.md` §4）。
本文件把这条约束与产品侧的「默认英文」分别钉死：

- 文案层：`sqlagent/i18n.py` 的中英一一对应 + 未知语言回落；
- 提示词：`prompts.py` 的四个 `*_prompt(lang)` 选择器（默认必须**就是**原对象）；
- 边界层话术：`server/messages.py` 模块级常量保持中文（既有导入/测试依赖），英文走 `text()`；
- 语言贯通：`ConversationBI(lang="en")` 的越界 / 澄清话术为英文，zh 下逐字不变；
- 落库与迁移：`sessions.lang` 列 + 老库补列（幂等）；
- API：`lang` 随会话落库、404 话术随 `X-Lang`、降级话术随会话语言。

零网络：`sqlagent.llm` 的两个入口换成假实现（手法同 `test_conversation_graph.py`）。
"""

from __future__ import annotations

import sqlite3
from itertools import chain
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server.app as server_app
from server import messages, store
from sqlagent import cli, i18n, llm, prompts
from sqlagent.conversation import ConversationBI
from sqlagent.gates import rewrite_followup, scope_check
from sqlagent.schemas import ScopeJudgement, TableSelection


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #

@pytest.fixture
def patch_llm(monkeypatch):
    """安装假 LLM：`scope` 可给单个 / 一串（按调用顺序消费），`tables` 固定。"""

    def install(*, scope, tables=("tracks",), texts=()):
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
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.sqlite"))
    monkeypatch.setenv("APP_PASSCODE", "")
    with TestClient(server_app.app, raise_server_exceptions=False) as c:
        yield c


# --------------------------------------------------------------------------- #
# 文案层：sqlagent/i18n.py
# --------------------------------------------------------------------------- #

def test_i18n_every_entry_is_bilingual_and_non_empty():
    assert i18n._TEXT, "文案表不能为空"
    for name, entry in i18n._TEXT.items():
        assert set(entry) == set(i18n.LANGS), f"{name} 缺语言"
        assert entry["zh"].strip(), f"{name} 中文为空"
        assert entry["en"].strip(), f"{name} 英文为空"
        assert entry["zh"] != entry["en"], f"{name} 中英相同（多半漏翻）"


def test_i18n_default_lang_is_zh_and_unchanged():
    """核心库默认必须是 zh —— 这是 §4「既有行为逐字不变」的根。"""
    assert i18n.DEFAULT_LANG == "zh"
    for name in i18n._TEXT:
        assert i18n.text(name) == i18n.text(name, "zh")


@pytest.mark.parametrize(("raw", "expected"), [
    (None, "zh"), ("", "zh"), ("   ", "zh"),
    ("zh", "zh"), ("en", "en"),
    ("EN", "en"), ("  En  ", "en"),
    ("zh-Hans", "zh"), ("en-US", "en"), ("en_US", "en"),
    ("fr", "zh"), ("english", "zh"),          # 认不出的按默认处理
])
def test_i18n_normalise_tolerates_case_and_region(raw, expected):
    assert i18n.normalise(raw) == expected


# --------------------------------------------------------------------------- #
# 提示词选择器：默认**就是**原中文对象（逐字不变的那条不变量）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("selector", "zh_name", "en_name"), [
    ("summarize_prompt", "SUMMARIZE_PROMPT", "SUMMARIZE_PROMPT_EN"),
    ("scope_prompt", "SCOPE_PROMPT", "SCOPE_PROMPT_EN"),
    ("rewrite_followup_prompt", "REWRITE_FOLLOWUP_PROMPT", "REWRITE_FOLLOWUP_PROMPT_EN"),
    ("summarize_history_prompt", "SUMMARIZE_HISTORY_PROMPT", "SUMMARIZE_HISTORY_PROMPT_EN"),
])
def test_prompt_selectors_default_to_zh(selector, zh_name, en_name):
    pick = getattr(prompts, selector)
    assert pick() is getattr(prompts, zh_name)          # 默认 = 原对象，非副本
    assert pick("zh") is getattr(prompts, zh_name)
    assert pick("en") is getattr(prompts, en_name)
    assert getattr(prompts, zh_name) is not getattr(prompts, en_name)


# --------------------------------------------------------------------------- #
# 边界层话术：server/messages.py
# --------------------------------------------------------------------------- #

def test_messages_module_constants_stay_chinese():
    """模块级常量仍是中文（既有导入 / `test_server_api.py` 的断言依赖它们）。"""
    assert messages.text("internal_error") == messages.INTERNAL_ERROR
    assert messages.text("llm_unavailable") == messages.LLM_UNAVAILABLE
    assert messages.text("session_not_found") == messages.SESSION_NOT_FOUND
    assert messages.text("passcode_required") == messages.PASSCODE_REQUIRED
    assert messages.text("passcode_invalid") == messages.PASSCODE_INVALID
    assert messages.text("quota_exceeded_global") == messages.QUOTA_EXCEEDED_GLOBAL
    assert messages.text("quota_exceeded_session") == messages.QUOTA_EXCEEDED_SESSION


@pytest.mark.parametrize("name", sorted(messages._ZH))
def test_messages_text_is_bilingual(name):
    zh, en = messages.text(name, "zh"), messages.text(name, "en")
    assert zh.strip() and en.strip() and zh != en
    assert messages.text(name, "de") == zh                # 未知语言回落
    assert messages.text(name, "en-US") == en             # 区域后缀容错


# --------------------------------------------------------------------------- #
# 闸门话术：gates.py 按语言取用
# --------------------------------------------------------------------------- #

def test_scope_check_unparsable_uses_language(parser, patch_llm):
    patch_llm(scope=None)                                  # 结构化输出解析失败
    zh = scope_check("随便问问", parser, lang="zh")
    en = scope_check("just asking", parser, lang="en")
    assert zh["reason"] == i18n.text("scope_unparsable", "zh")
    assert zh["clarify"] == i18n.text("clarify_fallback", "zh")
    assert en["reason"] == i18n.text("scope_unparsable", "en")
    assert en["clarify"] == i18n.text("clarify_fallback", "en")


def test_rewrite_followup_history_labels_follow_lang(monkeypatch):
    seen: dict[str, str] = {}

    def fake(messages, **kwargs):
        seen["text"] = "\n".join(str(getattr(m, "content", m)) for m in messages)
        return "standalone"

    monkeypatch.setattr(llm, "invoke_messages", fake)
    history = [("有多少位歌手？", "275 位。")]

    rewrite_followup("它有多少张专辑？", history, lang="en")
    assert "User: 有多少位歌手？" in seen["text"]
    assert "Assistant: 275 位。" in seen["text"]

    rewrite_followup("它有多少张专辑？", history, lang="zh")
    assert "用户：有多少位歌手？" in seen["text"]
    assert "助手：275 位。" in seen["text"]


# --------------------------------------------------------------------------- #
# 语言贯通：ConversationBI 整链路
# --------------------------------------------------------------------------- #

def test_conversation_en_out_of_scope_speaks_english(parser, patch_llm):
    patch_llm(scope=ScopeJudgement(scope="out", reason="not about music data", tables=[]))
    bot = ConversationBI(parser, thread_id="en-out", lang="en")

    trace = bot.ask("What's the weather today?")

    assert trace["scope"]["scope"] == "out"
    assert trace["answer"].startswith("Sorry, this question is outside")
    assert "Try asking about sales" in trace["answer"]


def test_conversation_zh_out_of_scope_verbatim_unchanged(parser, patch_llm):
    """zh 下的谢绝话术必须与原版逐字一致（§4 不变量）。"""
    patch_llm(scope=ScopeJudgement(scope="out", reason="与音乐数据无关", tables=[]))
    bot = ConversationBI(parser, thread_id="zh-out")

    trace = bot.ask("今天天气如何？")

    assert trace["answer"] == i18n.text("out_of_scope", "zh").format(reason="与音乐数据无关")


def test_conversation_en_clarify_payload_uses_model_wording(parser, patch_llm):
    patch_llm(scope=ScopeJudgement(scope="ambiguous", reason="vague", tables=[],
                                   clarify="Which year do you mean?"))
    bot = ConversationBI(parser, thread_id="en-clarify", lang="en")

    trace = bot.ask("how many were sold?")

    assert trace["status"] == "interrupted"
    assert trace["interrupt"]["type"] == "clarify"
    assert trace["interrupt"]["question"] == "Which year do you mean?"


def test_conversation_en_clarify_limit_answer_is_english(parser, patch_llm, monkeypatch):
    """澄清额度用尽后不再暂停，收尾话术随语言切换。"""
    from sqlagent.conversation import graph as conv_graph

    monkeypatch.setattr(conv_graph, "MAX_CLARIFY", 0)     # 第一次澄清即超限
    patch_llm(scope=ScopeJudgement(scope="ambiguous", reason="vague", tables=[],
                                   clarify="Which one?"))
    bot = ConversationBI(parser, thread_id="en-limit", lang="en")

    trace = bot.ask("how many?")

    assert trace["status"] == "done"
    assert trace["answer"] == i18n.text("clarify_limit_answer", "en")


# --------------------------------------------------------------------------- #
# 落库与迁移：server/store.py 的 `sessions.lang`
# --------------------------------------------------------------------------- #

def test_store_roundtrips_lang(tmp_path):
    st = store.Store(store.connect(tmp_path / "s.sqlite"))
    st.create_session("s-en", "t-1", "graph", False, lang="en")
    st.create_session("s-default", "t-2", "agent", True)   # 不传 = 默认 zh
    assert st.get_session("s-en")["lang"] == "en"
    assert st.get_session("s-default")["lang"] == "zh"
    st.close()


_OLD_SESSIONS_SCHEMA = """
CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL,
    route        TEXT NOT NULL,
    human_review INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    rounds       INTEGER NOT NULL DEFAULT 0
);
"""


def test_store_migrates_old_db_without_lang(tmp_path):
    """升级前的状态库没有 `lang` 列：打开时补列，老会话语言按默认 `zh`。"""
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_SESSIONS_SCHEMA)
    conn.execute("INSERT INTO sessions (session_id, thread_id, route, human_review,"
                 " created_at, last_seen_at, rounds) VALUES ('old', 't-old', 'graph', 0,"
                 " '2026-01-01T00:00:00Z', '2999-01-01T00:00:00Z', 0)")
    conn.commit()
    conn.close()

    st = store.Store(store.connect(path))                  # 开库即迁移
    assert st.get_session("old")["lang"] == "zh"
    columns = {r[1] for r in st.conn.execute("PRAGMA table_info(sessions)").fetchall()}
    assert "lang" in columns
    st.close()

    store.Store(store.connect(path)).close()               # 再开一次：迁移必须幂等


# --------------------------------------------------------------------------- #
# CLI：`--lang` 透传到门面
# --------------------------------------------------------------------------- #

def test_cli_workflow_route_forwards_lang(parser):
    asker = cli._build_asker(SimpleNamespace(route="workflow", lang="en"), parser)
    assert asker.__self__.lang == "en"                    # ChatBI 的绑定方法


def test_cli_parses_lang_and_forwards_to_builder(parser, monkeypatch, capsys):
    captured: dict = {}

    def fake_build(args, parser):
        captured["lang"] = args.lang
        return lambda question, show_trace=False: {"answer": "ok", "rows": None}

    monkeypatch.setattr(cli, "_build_asker", fake_build)
    rc = cli.main(["有多少首歌？", "--lang", "en",
                   "--db", str(parser.engine.url.database)])

    assert rc == 0
    assert captured["lang"] == "en"
    assert "ok" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# API：语言随会话落库 + 话术随语言
# --------------------------------------------------------------------------- #

def test_api_default_session_lang_is_zh(client):
    assert client.post("/api/session", json={}).json()["lang"] == "zh"


def test_api_session_lang_en_roundtrip_and_english_answer(client, patch_llm):
    patch_llm(scope=ScopeJudgement(scope="out", reason="not about music data", tables=[]))
    created = client.post("/api/session", json={"lang": "en"}).json()
    assert created["lang"] == "en"

    env = client.post("/api/chat", json={"session_id": created["session_id"],
                                         "message": "What's the weather today?"},
                      headers={"X-Lang": "en"}).json()
    assert env["trace"]["answer"].startswith("Sorry, this question is outside")


def test_api_unknown_session_message_follows_request_lang(client):
    """会话不存在时还查不到会话语言，用 `X-Lang` 请求头兜底（`app._request_lang`）。"""
    r = client.post("/api/chat", json={"session_id": "nope", "message": "hi"},
                    headers={"X-Lang": "en"})
    assert r.status_code == 404
    assert "doesn't exist or has expired" in r.json()["detail"]

    r = client.post("/api/chat", json={"session_id": "nope", "message": "hi"})
    assert r.status_code == 404
    assert r.json()["detail"] == messages.SESSION_NOT_FOUND
