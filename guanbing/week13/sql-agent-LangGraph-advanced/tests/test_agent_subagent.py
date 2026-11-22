"""批 5：SQL 生成子代理（D21–D23）。

要钉住三件事，全部离线（脚本假模型 + 真 chinook + 真 `create_agent` 图）：

1. **上下文边界**（D22）：子代理只拿问题文本，自己用 `list_tables` / `parse_table` 探索 schema；
   父图拿到的只有一条 `SqlDraft` 摘要 —— 主 context 里**不该出现** schema 原文。
2. **输出契约**（D23）：`{sql, tables, attempts, note}` 逐项正确，并回填进 `trace.sql` / `trace.attempts`；
   自纠在子代理自己的窗口里完成，父图看不到报错来回。
3. **闸门位置**（D23）：子代理**不产生** `interrupt()`；`HumanInTheLoopMiddleware` 只拦父图的 `run_sql`。

父、子代理共用同一个脚本模型实例，所以调用顺序是「父 → 子 → 子 → 父 → 父」这种确定序列：
脚本假模型的调用计数是共享的，工具又是在父图里**同步**跑的，顺序可预期。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

import sqlagent.agents.session as agent_session
import sqlagent.agents.sql_agent as sql_agent_module
from sqlagent import llm
from sqlagent.agents.session import AgentBI
from sqlagent.agents.sql_agent import (SqlDraft, build_autonomous_agent, build_sql_subagent,
                                       draft_sql, make_write_sql_tool)
from tests.test_agent_route import ScriptedModel

QUESTION = "数据库里一共有多少首歌曲？"
GOOD_SQL = "SELECT COUNT(*) FROM tracks;"
BAD_SQL = "SELECT * FROM 不存在的表;"
WRITE_SQL = "DROP TABLE tracks;"
ANSWER = "共有 3503 首歌曲。"


def call(name: str, args: dict, id_: str) -> AIMessage:
    """造一条「请求调用某工具」的 AI 消息。"""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args,
                                              "id": id_, "type": "tool_call"}])


def tool_names(graph) -> list:
    """取编译图里 tools 节点装载的工具名（`ToolNode.tools_by_name`）。"""
    return sorted(graph.nodes["tools"].bound.tools_by_name)


@pytest.fixture
def no_fallback(monkeypatch):
    """关掉兜底配置，让装配结果不依赖本机 `.env`。"""
    monkeypatch.setattr(sql_agent_module, "get_fallback_settings", lambda *a, **k: None)


@pytest.fixture
def inject_model(monkeypatch):
    """把 `AgentBI` 内部构造的模型换成脚本假模型（middleware / checkpointer 保持真实）。"""
    def install(model) -> None:
        real = sql_agent_module.build_autonomous_agent
        monkeypatch.setattr(
            agent_session, "build_autonomous_agent",
            lambda parser, **kw: real(parser, model=model, **kw),
        )
    return install


# --------------------------------------------------------------------------- #
# 上下文边界（D22）
# --------------------------------------------------------------------------- #

def test_subagent_gets_only_schema_tools_and_parent_gets_write_sql(parser):
    """子代理工具 = 只读地「看结构」的两个；`write_sql` 只在父图。"""
    assert tool_names(build_sql_subagent(parser)) == ["list_tables", "parse_table"]
    assert tool_names(build_autonomous_agent(parser)) == ["list_tables", "parse_table",
                                                          "run_sql", "write_sql"]


def test_write_sql_tool_takes_only_the_question(parser):
    """工具的入参里**没有** schema 的位置（D22）——父 agent 想塞也塞不进来。"""
    tool_obj = make_write_sql_tool(parser)
    assert tool_obj.name == "write_sql"
    assert set(tool_obj.args_schema.model_json_schema()["properties"]) == {"question"}


def test_parent_context_never_sees_schema_text(parser, no_fallback, inject_model):
    """跑完整一轮后：父 context 里只有摘要，没有 schema 原文（D22 的核心证据）。"""
    inject_model(ScriptedModel(script=[
        call("write_sql", {"question": QUESTION}, "c1"),        # 父：起草 SQL
        call("parse_table", {"table_name": "tracks"}, "c2"),    # 子：自己去看结构
        AIMessage(content=GOOD_SQL),                            # 子：交草稿
        call("run_sql", {"sql": GOOD_SQL}, "c3"),               # 父：执行
        AIMessage(content=ANSWER),                              # 父：结论
    ]))
    bot = AgentBI(parser, human_review=False, thread_id="subagent-context")

    trace = bot.ask(QUESTION)

    assert trace["sql"] == GOOD_SQL
    assert trace["attempts"] == 1
    assert trace["answer"] == ANSWER

    texts = "\n".join(llm.content_to_text(getattr(m, "content", "")) for m in bot._messages)
    assert '"attempts":1' in texts   # 子代理唯一的产物 = 这条摘要，父 context 里就它一条
    assert "TrackId" not in texts    # 但 `parse_table` 吐出的字段表**没**进父 context
    assert "CREATE TABLE" not in texts


# --------------------------------------------------------------------------- #
# 输出契约（D23）：摘要字段 + 自纠闭环
# --------------------------------------------------------------------------- #

def test_draft_sql_explores_schema_then_returns_summary(parser):
    """先 `parse_table`、再出 SQL：摘要把用到的表和次数都带上。"""
    model = ScriptedModel(script=[
        call("parse_table", {"table_name": "tracks"}, "c1"),
        AIMessage(content=GOOD_SQL),
    ])

    draft = draft_sql(parser, QUESTION, model=model)

    assert isinstance(draft, SqlDraft)
    assert draft.sql == GOOD_SQL
    assert draft.tables == ["tracks"]
    assert draft.attempts == 1
    assert draft.note == ""
    assert model.calls == 2          # 一次看结构 + 一次出 SQL


def test_draft_sql_self_corrects_inside_the_clean_window(parser):
    """第一次给坏 SQL，子代理在自己的窗口里看到报错并改对；父图只拿到最终版。"""
    model = ScriptedModel(script=[AIMessage(content=BAD_SQL), AIMessage(content=GOOD_SQL)])

    draft = draft_sql(parser, QUESTION, model=model)

    assert draft.sql == GOOD_SQL
    assert draft.attempts == 2
    assert draft.note == ""          # 最终成功，不留失败原因


def test_draft_sql_gives_up_at_max_attempts(parser):
    """一直改不对就到上限，把最后一版的失败原因交回去（`note`），不静默。"""
    model = ScriptedModel(script=[AIMessage(content=BAD_SQL)])

    draft = draft_sql(parser, QUESTION, model=model, max_attempts=2)

    assert draft.attempts == 2
    assert draft.sql == BAD_SQL
    assert draft.note                # 有原因，供前端与审计归因


def test_draft_sql_reports_guard_rejection(parser):
    """写操作被只读守卫拦下时，一样算一次尝试并把原因写进 `note`。"""
    model = ScriptedModel(script=[AIMessage(content=WRITE_SQL)])

    draft = draft_sql(parser, QUESTION, model=model, max_attempts=1)

    assert draft.attempts == 1
    assert draft.note
    assert draft.sql == WRITE_SQL


# --------------------------------------------------------------------------- #
# 闸门位置（D23）：子代理不中断，人工闸门只拦父图的 `run_sql`
# --------------------------------------------------------------------------- #

def test_subagent_never_interrupts_and_gate_stays_on_parent_run_sql(parser, no_fallback,
                                                                   inject_model):
    inject_model(ScriptedModel(script=[
        call("write_sql", {"question": QUESTION}, "c1"),
        AIMessage(content=GOOD_SQL),                            # 子代理（无需看结构）
        call("run_sql", {"sql": GOOD_SQL}, "c2"),               # 父：被闸门拦下
        AIMessage(content=ANSWER),
    ]))
    bot = AgentBI(parser, human_review=True, thread_id="subagent-gate")

    trace = bot.ask(QUESTION)

    assert trace["status"] == "interrupted"
    reqs = trace["interrupt"]["action_requests"]
    # 只有 `run_sql` 一项：`write_sql`（内部是一个 create_agent 子图）没有产生第二次中断
    assert [r["name"] for r in reqs] == ["run_sql"]
    assert reqs[0]["args"]["sql"] == GOOD_SQL

    trace = bot.resume({"decisions": [{"type": "approve"}]})
    assert trace["status"] == "done"
    assert trace["sql"] == GOOD_SQL
    assert trace["attempts"] == 1        # 中断前子代理已经交过草稿
    assert trace["answer"] == ANSWER


def test_attempts_is_zero_without_the_subagent(parser, no_fallback, inject_model):
    """老剧本（模型直接 `run_sql`，不经过 `write_sql`）照旧跑，`attempts` 记 0。"""
    inject_model(ScriptedModel(script=[
        call("run_sql", {"sql": GOOD_SQL}, "c1"),
        AIMessage(content=ANSWER),
    ]))
    bot = AgentBI(parser, human_review=False, thread_id="subagent-absent")

    trace = bot.ask(QUESTION)

    assert trace["sql"] == GOOD_SQL
    assert trace["attempts"] == 0
