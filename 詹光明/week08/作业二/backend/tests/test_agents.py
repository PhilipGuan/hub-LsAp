"""各 agent 的契约与兜底行为。

重点验三件事：
1. **程序赋值优先于 LLM 自述** —— 笔记的 sid 由程序写死，不采信模型输出。
2. **LLM 挂了不抛异常** —— 每个 agent 都要有自己的降级路径。
3. **LLM 编的 id 会被丢掉** —— 反思里出现不存在的 qid 不能污染覆盖率统计。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.planner import plan
from app.agents.reader import MIN_READABLE_CHARS, read_many, read_source
from app.agents.reflector import reflect
from app.llm import LLMError, LLMJSONError, LLMTruncatedError
from app.models import ContentOrigin, Source, SubQuestion


class _StubLLM:
    """按需返回预设结果或抛预设异常的假 client。

    ``script`` 给需要「第一次失败、第二次成功」的用例：按序吐结果或抛异常，
    用完后一直重复最后一个。
    """

    # 被截断后重试用的预算（真 client 上是从 settings 读的属性）
    retry_max_tokens = 32768

    def __init__(
        self,
        result: Any = None,
        error: Exception | None = None,
        *,
        script: list[Any] | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self._script = list(script) if script else None
        self.calls: list[str] = []
        self.kwargs: list[dict[str, Any]] = []

    async def complete_json(self, prompt: str, model_cls: Any, **kwargs: Any) -> Any:
        self.calls.append(prompt)
        self.kwargs.append(kwargs)
        if self._script is not None:
            item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
            if isinstance(item, Exception):
                raise item
            return item
        if self._error is not None:
            raise self._error
        assert self._result is not None, "stub 没配结果"
        return self._result


def _source(
    sid: str = "S1",
    *,
    content: str = "一段足够长的正文内容。" * 10,
    origin: ContentOrigin = ContentOrigin.page,
    title: str = "标题",
) -> Source:
    return Source(
        sid=sid,
        url=f"https://example.com/{sid}",
        normalized_url=f"https://example.com/{sid}",
        title=title,
        content=content,
        content_chars=len(content),
        content_origin=origin,
    )


# ══════════════════════════════════════════════════════════════
# planner
# ══════════════════════════════════════════════════════════════


class TestPlanner:
    async def test_assigns_sequential_qids(self) -> None:
        from app.agents.planner import PlannerOutput, _PlannedQuestion

        llm = _StubLLM(
            PlannerOutput(
                sub_questions=[
                    _PlannedQuestion(text="子问题一", queries=["检索甲"]),
                    _PlannedQuestion(text="子问题二", queries=["检索乙"]),
                ],
                queries=["检索甲", "检索乙"],
                rationale="思路",
            )
        )
        result = await plan("某主题", llm)  # type: ignore[arg-type]

        assert [q.qid for q in result.sub_questions] == ["q1", "q2"]
        assert result.queries == ["检索甲", "检索乙"]
        assert result.degraded is False

    async def test_queries_deduped_and_capped(self) -> None:
        from app.agents.planner import PlannerOutput, _PlannedQuestion

        llm = _StubLLM(
            PlannerOutput(
                sub_questions=[_PlannedQuestion(text="子问题")],
                queries=["甲", "甲", " 甲 ", "乙", "丙", "丁"],
            )
        )
        result = await plan("某主题", llm, queries_per_round=2)  # type: ignore[arg-type]

        assert result.queries == ["甲", "乙"]

    async def test_falls_back_to_per_question_queries(self) -> None:
        """顶层 queries 为空时，用子问题自带的检索词补上。"""
        from app.agents.planner import PlannerOutput, _PlannedQuestion

        llm = _StubLLM(
            PlannerOutput(
                sub_questions=[
                    _PlannedQuestion(text="子问题一", queries=["检索甲"]),
                    _PlannedQuestion(text="子问题二", queries=["检索乙"]),
                ],
                queries=[],
            )
        )
        result = await plan("某主题", llm)  # type: ignore[arg-type]

        assert result.queries == ["检索甲", "检索乙"]

    async def test_llm_failure_uses_template_fallback(self) -> None:
        llm = _StubLLM(error=LLMError("网络炸了"))
        result = await plan("量子计算", llm, max_sub_questions=3)  # type: ignore[arg-type]

        assert result.degraded is True
        assert len(result.sub_questions) == 3
        assert result.queries  # 兜底也必须给出检索词，否则整个流程跑不动
        assert all("量子计算" in q.text for q in result.sub_questions)

    async def test_empty_sub_questions_uses_fallback(self) -> None:
        from app.agents.planner import PlannerOutput

        llm = _StubLLM(PlannerOutput(sub_questions=[], queries=["甲"]))
        result = await plan("某主题", llm)  # type: ignore[arg-type]

        assert result.degraded is True
        assert len(result.sub_questions) == 4

    async def test_json_error_also_caught(self) -> None:
        """LLMJSONError 是 LLMError 的子类，兜底路径要能一起接住。"""
        llm = _StubLLM(error=LLMJSONError("三层修复链全挂"))
        result = await plan("某主题", llm)  # type: ignore[arg-type]

        assert result.degraded is True


# ══════════════════════════════════════════════════════════════
# reader
# ══════════════════════════════════════════════════════════════


class TestReader:
    async def test_sid_comes_from_program_not_llm(self) -> None:
        """LLM 只产出主张；来源编号由程序写死 —— 这是引用可追溯的前提。"""
        from app.agents.reader import ReaderOutput, _ReadNote

        llm = _StubLLM(
            ReaderOutput(
                notes=[_ReadNote(claim="某条事实", evidence="依据", strength=0.9)],
                summary="一句话",
            )
        )
        notes = await read_source(_source("S7"), "子问题", llm)  # type: ignore[arg-type]

        assert len(notes) == 1
        assert notes[0].sid == "S7"
        assert notes[0].note_id == "S7-N1"

    async def test_strength_clamped(self) -> None:
        from app.agents.reader import ReaderOutput, _ReadNote

        llm = _StubLLM(
            ReaderOutput(
                notes=[_ReadNote(claim="甲", strength=1.8), _ReadNote(claim="乙", strength=-2)]
            )
        )
        notes = await read_source(_source(), "子问题", llm)  # type: ignore[arg-type]

        assert notes[0].strength == 1.0
        assert notes[1].strength == 0.0

    async def test_snippet_origin_marks_weak(self) -> None:
        from app.agents.reader import ReaderOutput, _ReadNote

        llm = _StubLLM(ReaderOutput(notes=[_ReadNote(claim="甲")]))
        notes = await read_source(
            _source(origin=ContentOrigin.snippet),
            "子问题",
            llm,  # type: ignore[arg-type]
        )

        assert notes[0].weak is True

    async def test_page_origin_not_weak(self) -> None:
        from app.agents.reader import ReaderOutput, _ReadNote

        llm = _StubLLM(ReaderOutput(notes=[_ReadNote(claim="甲")]))
        notes = await read_source(_source(), "子问题", llm)  # type: ignore[arg-type]

        assert notes[0].weak is False

    async def test_short_content_skips_llm_entirely(self) -> None:
        llm = _StubLLM(error=RuntimeError("不该被调到"))
        notes = await read_source(_source(content="太短"), "子问题", llm)  # type: ignore[arg-type]

        assert notes == []
        assert llm.calls == []

    async def test_empty_notes_is_fine(self) -> None:
        """LLM 判断这篇内容无关 —— 返回空列表是正常结果，不是错误。"""
        from app.agents.reader import ReaderOutput

        llm = _StubLLM(ReaderOutput(notes=[]))
        notes = await read_source(_source(), "子问题", llm)  # type: ignore[arg-type]

        assert notes == []

    async def test_llm_failure_returns_empty_not_raise(self) -> None:
        llm = _StubLLM(error=LLMError("超时"))
        notes = await read_source(_source(), "子问题", llm)  # type: ignore[arg-type]

        assert notes == []

    async def test_blank_claims_dropped(self) -> None:
        from app.agents.reader import ReaderOutput, _ReadNote

        llm = _StubLLM(ReaderOutput(notes=[_ReadNote(claim="  "), _ReadNote(claim="有效主张")]))
        notes = await read_source(_source(), "子问题", llm)  # type: ignore[arg-type]

        assert [n.claim for n in notes] == ["有效主张"]

    async def test_read_many_skips_unreadable_sources(self) -> None:
        """readable=False 的来源不该浪费一次 LLM 调用。"""
        from app.agents.reader import ReaderOutput, _ReadNote

        llm = _StubLLM(ReaderOutput(notes=[_ReadNote(claim="甲")]))
        duplicate = _source("S2")
        duplicate.duplicate_of = "S1"

        notes = await read_many([_source("S1"), duplicate], "子问题", llm)  # type: ignore[arg-type]

        assert [n.sid for n in notes] == ["S1"]
        assert len(llm.calls) == 1

    async def test_min_readable_chars_boundary(self) -> None:
        from app.agents.reader import ReaderOutput, _ReadNote

        llm = _StubLLM(ReaderOutput(notes=[_ReadNote(claim="甲")]))
        exactly = "字" * MIN_READABLE_CHARS

        notes = await read_source(_source(content=exactly), "子问题", llm)  # type: ignore[arg-type]

        assert len(notes) == 1


class TestReaderTruncationRetry:
    """被 max_tokens 截断是**可恢复**的，不能当成「这页读不了」直接丢掉。

    实测：deepseek-flash 读 Rust 调度器、量化政策分析这类长文，思考能到
    1.5~2.3 万字，8192 的预算照样吃满，content 一个字不吐。而这些页面
    本身完全读得下来 —— 不重试就等于白白扔掉 5/30 条来源。
    """

    async def test_truncation_retries_with_bigger_budget(self) -> None:
        from app.agents.reader import ReaderOutput, _ReadNote

        llm = _StubLLM(
            script=[
                LLMTruncatedError("空 content"),
                ReaderOutput(notes=[_ReadNote(claim="重试后抽到了")]),
            ]
        )

        notes = await read_source(_source(), "子问题", llm)  # type: ignore[arg-type]

        assert [n.claim for n in notes] == ["重试后抽到了"]
        assert len(llm.calls) == 2
        assert llm.kwargs[0].get("max_tokens") is None  # 首次走默认预算
        assert llm.kwargs[1]["max_tokens"] == 32768  # 只有重试才加大

    async def test_retry_also_truncated_gives_up_honestly(self) -> None:
        """重试也失败就认账记 0 条笔记 —— 不硬凑主张。"""
        llm = _StubLLM(script=[LLMTruncatedError("空 content")])

        notes = await read_source(_source(), "子问题", llm)  # type: ignore[arg-type]

        assert notes == []
        assert len(llm.calls) == 2

    async def test_plain_llm_error_does_not_retry(self) -> None:
        """超时/网络这类失败重试同一份 prompt 没有意义，别白烧一次调用。"""
        llm = _StubLLM(error=LLMError("超时"))

        notes = await read_source(_source(), "子问题", llm)  # type: ignore[arg-type]

        assert notes == []
        assert len(llm.calls) == 1


# ══════════════════════════════════════════════════════════════
# reflector
# ══════════════════════════════════════════════════════════════


def _sub_questions() -> list[SubQuestion]:
    return [
        SubQuestion(qid="q1", text="子问题一"),
        SubQuestion(qid="q2", text="子问题二"),
    ]


class TestReflector:
    async def test_hallucinated_qids_dropped(self) -> None:
        """LLM 编出的 q9 不能进覆盖率字典 —— 那会让统计凭空多出几项。"""
        from app.agents.reflector import ReflectorOutput, _Gap, _NextQuery

        llm = _StubLLM(
            ReflectorOutput(
                sufficient=False,
                coverage={"q1": 0.9, "q9": 0.5},
                gaps=[_Gap(qid="q9", gap="编出来的缺口"), _Gap(qid="q2", gap="真的缺口")],
                next_queries=[_NextQuery(qid="q9", query="编的检索词")],
            )
        )
        outcome = await reflect(
            "主题",
            _sub_questions(),
            [],
            [],
            llm,
            round_no=1,
            max_rounds=3,  # type: ignore[arg-type]
        )

        assert outcome.coverage == {"q1": 0.9}
        # 缺口内容本身是有效的，只是 qid 编错了 —— 置空而不是整条丢掉，
        # 丢掉会把「它确实指出了某个缺口」这个信息一起弄没
        assert [g["qid"] for g in outcome.gaps] == ["", "q2"]
        assert [g["gap"] for g in outcome.gaps] == ["编出来的缺口", "真的缺口"]
        assert [q["query"] for q in outcome.next_queries] == ["编的检索词"]
        assert outcome.next_queries[0]["qid"] == ""

    async def test_coverage_clamped(self) -> None:
        from app.agents.reflector import ReflectorOutput

        llm = _StubLLM(ReflectorOutput(coverage={"q1": 1.7, "q2": -0.3}))
        outcome = await reflect(
            "主题",
            _sub_questions(),
            [],
            [],
            llm,
            round_no=1,
            max_rounds=3,  # type: ignore[arg-type]
        )

        assert outcome.coverage == {"q1": 1.0, "q2": 0.0}

    async def test_llm_failure_says_sufficient(self) -> None:
        """反思失败时按「够了」收尾，但标记 degraded 让引擎知道这是兜底。"""
        llm = _StubLLM(error=LLMError("挂了"))
        outcome = await reflect(
            "主题",
            _sub_questions(),
            [],
            [],
            llm,
            round_no=1,
            max_rounds=3,  # type: ignore[arg-type]
        )

        assert outcome.sufficient is True
        assert outcome.degraded is True
        assert outcome.next_queries == []

    @pytest.mark.parametrize("bad_qid", ["", "未知"])
    async def test_unknown_qid_blanked(self, bad_qid: str) -> None:
        from app.agents.reflector import ReflectorOutput, _Gap

        llm = _StubLLM(ReflectorOutput(gaps=[_Gap(qid=bad_qid, gap="某缺口")]))
        outcome = await reflect(
            "主题",
            _sub_questions(),
            [],
            [],
            llm,
            round_no=1,
            max_rounds=3,  # type: ignore[arg-type]
        )

        assert outcome.gaps[0]["qid"] == ""
        assert outcome.gaps[0]["gap"] == "某缺口"
