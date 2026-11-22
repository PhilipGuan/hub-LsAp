"""进阶会话图测试：持久化 / 澄清续接 / 人工介入（零网络，假 LLM）。

会话图（`ConversationBI`）与上一版的 `ChatBI` 的差异集中在三点，本文件逐点钉住：
- 整轮对话是一张图，`turns` 随 `thread_id` 落在检查点里（持久化 + 多轮记忆）；
- `ambiguous` 分支用 `interrupt()` 暂停、`resume()` 续接（消灭「澄清只能单轮」）；
- `human_review=True` 时，SQL 执行前有人工复核闸门（approve / edit / reject）。
"""

from __future__ import annotations

import pytest

from sqlagent import llm
from sqlagent.conversation import ConversationBI
from sqlagent.schemas import ScopeJudgement, TableSelection
from sqlagent.session import make_checkpointer, thread_config

GOOD_SQL = "SELECT COUNT(*) FROM tracks;"


@pytest.fixture
def patch_llm(monkeypatch):
    """安装假 LLM：scope 可给单个或一串（按调用顺序消费），tables 固定。"""

    def install(*, scope, tables=("tracks",), texts):
        from itertools import chain

        scopes = list(scope) if isinstance(scope, list) else [scope]
        scope_it = iter(chain(scopes, [None] * 10))  # 用尽后返回 None（模拟解析失败）

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


# --------------------------------------------------------------------------- #
# checkpointer 工厂
# --------------------------------------------------------------------------- #

def test_make_checkpointer_memory_and_sqlite(tmp_path):
    assert type(make_checkpointer("memory")).__name__ == "InMemorySaver"
    assert type(make_checkpointer("sqlite", str(tmp_path / "ck.sqlite"))).__name__ == "SqliteSaver"


def test_make_checkpointer_rejects_unknown_kind():
    with pytest.raises(ValueError):
        make_checkpointer("redis")


def test_thread_config_shape():
    assert thread_config("t-1") == {"configurable": {"thread_id": "t-1"}}


# --------------------------------------------------------------------------- #
# 拓扑
# --------------------------------------------------------------------------- #

def test_conversation_topology(parser):
    bot = ConversationBI(parser, thread_id="topo")
    nodes = set(bot.graph.get_graph().nodes)
    assert {"summarize", "rewrite", "scope", "clarify", "sql", "reject", "finalize"} <= nodes


# --------------------------------------------------------------------------- #
# [in] 分支 + 多轮记忆（持久化）
# --------------------------------------------------------------------------- #

def test_in_branch_runs_subgraph_and_records_turn(parser, patch_llm):
    patch_llm(scope=ScopeJudgement(scope="in", reason="统计歌曲", tables=["tracks"]),
              texts=[GOOD_SQL, "共有 3503 首歌曲。"])
    bot = ConversationBI(parser, thread_id="t-in")

    trace = bot.ask("数据库里一共有多少首歌曲？")

    assert trace["status"] == "done"
    assert trace["ok"] is True
    assert trace["rows"] == [(3503,)]
    assert trace["standalone_question"] == "数据库里一共有多少首歌曲？"
    # turns 落在检查点里（不是 Python 内存）——读自 graph.get_state
    assert bot.turns == [("数据库里一共有多少首歌曲？", "共有 3503 首歌曲。")]


def test_turns_accumulate_across_asks(parser, patch_llm):
    """第二轮会触发 [A] 追问改写（有历史）；两轮都进 [in] 分支。"""
    patch_llm(
        scope=[ScopeJudgement(scope="in", reason="ok", tables=["tracks"]),
               ScopeJudgement(scope="in", reason="ok", tables=["tracks"])],
        texts=[GOOD_SQL, "共有 3503 首歌曲。",            # 第 1 轮：生成 + 汇总
               "那换个说法：数据库里一共有多少首歌曲？",   # 第 2 轮 [A] 改写
               GOOD_SQL, "共有 3503 首歌曲。"],           # 第 2 轮：生成 + 汇总
    )
    bot = ConversationBI(parser, thread_id="t-multi")
    bot.ask("数据库里一共有多少首歌曲？")
    trace = bot.ask("那再确认一遍")

    assert trace["status"] == "done"
    assert len(bot.turns) == 2
    assert bot.turns[1][0] == "那再确认一遍"


def test_summarize_compresses_long_history(parser, patch_llm, monkeypatch):
    """长对话：未摘要轮数超过阈值后触发一次历史摘要，并把它传给下一轮改写。

    把阈值压到 2、保留窗口压到 1，用 4 轮对话即可触发一次压缩。文本按调用顺序消费，
    顺序即「[S] 摘要 → [A] 改写 → 生成 → 汇总」——正好把摘要发生在改写之前的因果关系钉死。
    """
    import sqlagent.conversation.graph as cg
    monkeypatch.setattr(cg, "SUMMARY_TRIGGER", 2)
    monkeypatch.setattr(cg, "REWRITE_WINDOW", 1)

    scope = ScopeJudgement(scope="in", reason="ok", tables=["tracks"])
    patch_llm(
        scope=[scope] * 4,
        texts=[
            GOOD_SQL, "第 1 轮答案",                    # 第 1 轮（无历史，无摘要）
            "改写 2", GOOD_SQL, "第 2 轮答案",           # 第 2 轮：[A] + 生成 + 汇总
            "改写 3", GOOD_SQL, "第 3 轮答案",           # 第 3 轮：[A] + 生成 + 汇总
            "较早对话摘要", "改写 4", GOOD_SQL, "第 4 轮答案",  # 第 4 轮：[S] + [A] + 生成 + 汇总
        ],
    )
    bot = ConversationBI(parser, thread_id="t-summary")
    for i in range(1, 5):
        bot.ask(f"第 {i} 轮问题")

    snap = bot.graph.get_state(bot.config).values
    assert snap.get("history_summary") == "较早对话摘要"
    assert snap.get("summarized_upto") == 2


# --------------------------------------------------------------------------- #
# [out] 分支：绝不生成 SQL
# --------------------------------------------------------------------------- #

def test_out_branch_rejects_without_sql(parser, patch_llm):
    patch_llm(scope=ScopeJudgement(scope="out", reason="与数据库无关"), texts=[])
    bot = ConversationBI(parser, thread_id="t-out")

    trace = bot.ask("明天天气怎么样？")

    assert trace["status"] == "done"
    assert trace["sql"] is None and trace["rows"] is None
    assert "超出了当前 chinook 数据库的范围" in trace["answer"]


# --------------------------------------------------------------------------- #
# [ambiguous] 分支：interrupt 暂停 → resume 续接
# --------------------------------------------------------------------------- #

def test_ambiguous_interrupts_then_resume_continues(parser, patch_llm):
    patch_llm(
        scope=[ScopeJudgement(scope="ambiguous", reason="指代不清", clarify="你问的是哪张表？"),
               ScopeJudgement(scope="in", reason="统计歌曲", tables=["tracks"])],
        texts=[GOOD_SQL, "共有 3503 首歌曲。"],
    )
    bot = ConversationBI(parser, thread_id="t-clarify")

    trace = bot.ask("有多少？")
    assert trace["status"] == "interrupted"
    assert trace["interrupt"]["type"] == "clarify"
    assert "你问的是哪张表？" in trace["interrupt"]["question"]

    # 用户补充后，图从 clarify 续接回 rewrite → 走完 [in] 分支
    trace = bot.resume("数据库里一共有多少首歌曲？")
    assert trace["status"] == "done"
    assert trace["rows"] == [(3503,)]


def test_clarify_stops_at_max_and_resets_next_turn(parser, patch_llm):
    """澄清额度（D11）用尽后收尾，而不是无限暂停；下一轮重新计数（`finalize` 归零）。

    这一轮里 `scope` 永远是 ambiguous，所以图只会「反问 → 续接 → 再反问」循环，
    正好用来把上限钉死：第 1 次提问 + 2 次续接都在暂停，第 3 次续接就必须给出结论。
    """
    import sqlagent.conversation.graph as cg

    patch_llm(scope=ScopeJudgement(scope="ambiguous", reason="指代不清", clarify="你问的是哪张表？"),
              texts=["改写后的第二问"])          # 第 2 轮才会用到（第 1 轮无历史，不改写）
    bot = ConversationBI(parser, thread_id="t-clarify-limit")

    assert bot.ask("有多少？")["status"] == "interrupted"            # 第 1 次反问
    assert bot.resume("还是不清楚")["status"] == "interrupted"       # 第 2 次反问
    assert bot.resume("再想想")["status"] == "interrupted"           # 第 3 次反问

    trace = bot.resume("我也不知道")                                 # 额度已尽 → 收尾
    assert trace["status"] == "done"
    assert trace["ok"] is False
    assert trace["sql"] is None                    # 放弃时绝不生成 SQL
    assert "没能确定" in trace["answer"]
    assert cg.MAX_CLARIFY == 3                     # 与上面写死的三次反问对应

    # 下一轮：计数已被 finalize 归零，能重新反问（否则一进来就会直接放弃）
    assert bot.ask("再问一句")["status"] == "interrupted"


# --------------------------------------------------------------------------- #
# 人工复核（human_review=True）
# --------------------------------------------------------------------------- #

def test_human_review_approve_executes(parser, patch_llm):
    patch_llm(scope=ScopeJudgement(scope="in", reason="ok", tables=["tracks"]),
              texts=[GOOD_SQL, "共有 3503 首歌曲。"])
    bot = ConversationBI(parser, thread_id="t-hitl-ok", human_review=True)

    trace = bot.ask("数据库里一共有多少首歌曲？")
    assert trace["status"] == "interrupted"
    assert trace["interrupt"]["type"] == "sql_review"
    assert trace["interrupt"]["sql"] == GOOD_SQL

    trace = bot.resume({"action": "approve"})
    assert trace["status"] == "done"
    assert trace["ok"] is True
    assert trace["rows"] == [(3503,)]


def test_human_review_reject_fails_without_execute(parser, patch_llm):
    patch_llm(scope=ScopeJudgement(scope="in", reason="ok", tables=["tracks"]),
              texts=[GOOD_SQL])   # 只需生成一次；被驳回后不会走汇总
    bot = ConversationBI(parser, thread_id="t-hitl-no", human_review=True)

    assert bot.ask("数据库里一共有多少首歌曲？")["status"] == "interrupted"
    trace = bot.resume({"action": "reject", "note": "口径不对"})

    assert trace["status"] == "done"
    assert trace["ok"] is False
    assert trace["rows"] is None
    assert trace["answer"]


def test_human_review_edit_rechecks_then_executes(parser, patch_llm):
    """人工改写后：新 SQL 必须重新过 check，但**不再要求本人二次审批**。"""
    patch_llm(scope=ScopeJudgement(scope="in", reason="ok", tables=["tracks"]),
              texts=[GOOD_SQL, "共 347 张专辑。"])   # 生成 + （改后）汇总
    bot = ConversationBI(parser, thread_id="t-hitl-edit", human_review=True)

    trace = bot.ask("数据库里一共有多少首歌曲？")
    assert trace["status"] == "interrupted"
    assert trace["interrupt"]["sql"] == GOOD_SQL

    expected = parser.execute_sql("SELECT COUNT(*) FROM albums;")[1]
    trace = bot.resume({"action": "edit", "sql": "SELECT COUNT(*) FROM albums;"})

    assert trace["status"] == "done"      # 未再中断
    assert trace["ok"] is True
    assert trace["rows"] == expected
