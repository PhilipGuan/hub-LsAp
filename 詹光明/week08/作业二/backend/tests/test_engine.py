"""编排引擎的端到端测试 —— 全程不联网。

searcher / llm / fetcher 三个依赖全部可注入，所以整条状态机能在毫秒级跑穿。
这里验的是**编排逻辑**：轮次、终止、事件顺序、收尾保护；
引用与置信度的细则在 test_citation.py / test_confidence.py 里单独验。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.engine import run_research
from app.events import EventType
from app.models import (
    ContentOrigin,
    FetchStatus,
    RawSearchResult,
    ResearchRequest,
    SearchOutcome,
)

# 两个子问题的原文。用有实义词的短语而不是「子问题1」，
# 因为 _assign_note_qids 靠字符重合度归类，无实义词的编号会让归类失真。
Q1 = "市场主要玩家"
Q2 = "产品定价对比"

# ══════════════════════════════════════════════════════════════
# 测试替身
# ══════════════════════════════════════════════════════════════


class FakeLLM:
    """按**模型类名**路由到预设 JSON。

    刻意不按 prompt 里的特征词路由 —— 那样一改模板文案测试就集体失效，
    而模板文案本来就是要反复调的。
    """

    def __init__(self, *, plan=None, reader=None, reflect=None, head=None, section=None):
        self.calls: list[str] = []
        self.responses: dict[str, object] = {
            "PlannerOutput": plan,
            "ReaderOutput": reader,
            "ReflectorOutput": reflect,
            "SynthesisHead": head,
            "SectionOutput": section,
        }

    async def complete_json(self, prompt: str, model_cls, **kwargs):
        from app.llm import LLMError, validate_lenient

        name = model_cls.__name__
        self.calls.append(name)
        raw = self.responses.get(name)
        if callable(raw):  # 允许传函数做动态响应
            raw = raw()
        if raw is None:
            raise LLMError(f"fake: 未配置 {name} 的响应")
        return validate_lenient(model_cls, raw)

    async def complete(self, *args, **kwargs):
        raise AssertionError("引擎不应直接调用 complete()")

    async def aclose(self) -> None:
        pass


class FakeSearcher:
    """按 query 查预设结果。

    ``fail_all=True`` 模拟**接口整体不可用**（余额耗尽 / Key 失效），
    与「接口正常但这个主题搜不到东西」是两种不同的故障，分别对应
    来源饥饿与停滞两条终止路径。
    """

    def __init__(
        self,
        script: dict[str, list[dict]] | None = None,
        *,
        fail_all: bool = False,
        default: list[dict] | None = None,
    ):
        self.script = script or {}
        self.fail_all = fail_all
        # 没预设的 query 都返回它 —— 用于规划走模板兜底、检索词事先不可知的场景
        self.default = default
        self.queries: list[str] = []
        self.closed = False

    async def search(self, query: str, *, count=None, summary=True) -> SearchOutcome:
        self.queries.append(query)
        if self.fail_all:
            return SearchOutcome(query=query, ok=False, error="模拟接口不可用", code=429)
        raw = self.script.get(query, self.default)
        if raw is None:
            # 没预设就返回空结果 —— 模拟「搜了但没搜到新东西」
            return SearchOutcome(
                query=query, ok=True, results=[], matched_path="data.webPages.value"
            )
        return SearchOutcome(
            query=query,
            ok=True,
            results=[RawSearchResult(**r) for r in raw],
            matched_path="data.webPages.value",
        )

    async def aclose(self) -> None:
        self.closed = True


class FakeFetcher:
    """按域名决定抓取结果 —— 让降级链在测试里可控。"""

    def __init__(self, *, ok_domains=(), blocked_domains=()):
        self.ok_domains = set(ok_domains)
        self.blocked_domains = set(blocked_domains)
        self.sources: list = []

    async def fetch_many(self, sources):
        for source in sources:
            self.sources.append(source)
            source.fetched = True
            if any(d in source.url for d in self.blocked_domains):
                source.fetch_status = FetchStatus.blocked
                source.fetch_error = "在反爬黑名单中"
                self._degrade(source)
            elif any(d in source.url for d in self.ok_domains):
                source.fetch_status = FetchStatus.ok
                source.content = "正文内容。" * 60
                source.content_chars = len(source.content)
                source.content_origin = ContentOrigin.page
                source.extractor = "fake"
            else:
                source.fetch_status = FetchStatus.extract_failed
                source.fetch_error = "HTTP 200 但正文提取失败"
                self._degrade(source)
        return sources

    @staticmethod
    def _degrade(source) -> None:
        """抓不到就退到搜索摘要 / 片段，并如实标来路。"""
        source.content = source.search_summary or source.snippet or None
        source.content_chars = len(source.content or "")
        source.content_origin = (
            ContentOrigin.search_summary if source.search_summary else ContentOrigin.snippet
        )

    async def aclose(self) -> None:
        pass


# ── 各 agent 的假响应 ────────────────────────────────────────


def _result(url: str, title: str, *, summary: str = "", date: str | None = None) -> dict:
    return {
        "title": title,
        "url": url,
        "snippet": f"{title} 的描述",
        "summary": summary or f"{title} 的搜索摘要。" * 5,
        "site_name": title,
        "published_at": datetime.fromisoformat(date) if date else None,
    }


def _plan(n: int = 2) -> dict:
    texts = [Q1, Q2, "行业趋势", "潜在风险"][:n]
    return {
        "sub_questions": [
            {"text": text, "rationale": "理由", "queries": [f"q{i}"]}
            for i, text in enumerate(texts, 1)
        ],
        "queries": [f"q{i}" for i in range(1, n + 1)],
        "rationale": "测试用规划",
    }


def _reader(qids: tuple[int, ...] = (1, 2), per_qid: int = 2) -> dict:
    """每个子问题给若干条笔记 —— 3 条即算覆盖率满分（见 stopping.py）。"""
    texts = {1: Q1, 2: Q2}
    notes = [
        {
            "claim": f"{texts[i]}方面，甲站披露了具体事实 {j}",
            "evidence": "原文依据",
            "quote": "原文原句",
            "strength": 0.8,
        }
        for i in qids
        for j in range(1, per_qid + 1)
    ]
    return {"notes": notes, "summary": "一句话概括"}


def _reflect(*, sufficient: bool, next_queries: list[dict] | None = None) -> dict:
    return {
        "sufficient": sufficient,
        "coverage": {"q1": 1.0, "q2": 1.0},
        "gaps": [] if sufficient else [{"qid": "q1", "gap": "缺少官方数据"}],
        "next_queries": next_queries or [],
        "rationale": "测试用反思",
    }


def _head() -> dict:
    return {
        "title": "测试报告",
        "executive_summary": "这是摘要。",
        "key_conclusions": [{"text": "这是结论", "sids": ["S1"]}],
        "open_questions": ["还有个问题没解决"],
    }


def _section() -> dict:
    return {"heading": "分节标题", "body_md": "正文里提到了一些事实 [S1]。"}


async def _collect(**kwargs):
    events = []
    async for event in run_research(kwargs.pop("request"), **kwargs):
        events.append(event)
    return events


def _types(events) -> list[EventType]:
    return [e.type for e in events]


def _payload(events, event_type: EventType) -> dict:
    matches = [e for e in events if e.type is event_type]
    assert matches, f"没有 {event_type.value} 事件"
    return matches[-1].payload


def _settings(max_rounds: int = 3, **overrides):
    from app.config import settings as default_settings

    # 绝不原地改全局 settings —— 会污染同进程里的其它测试
    return default_settings.model_copy(
        update={
            "max_rounds": max_rounds,
            "bocha_api_key": "x",
            "llm_api_key": "x",
            "bocha_min_interval_s": 0.0,
            "fetch_domain_interval_s": 0.0,
            **overrides,
        }
    )


def _llm(**overrides) -> FakeLLM:
    """默认给全所有阶段的响应，单个测试只覆盖关心的那个。"""
    defaults = {
        "plan": _plan(),
        "reader": _reader(),
        "reflect": _reflect(sufficient=True),
        "head": _head(),
        "section": _section(),
    }
    return FakeLLM(**{**defaults, **overrides})


# ══════════════════════════════════════════════════════════════
# 主路径
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_full_run_produces_report():
    searcher = FakeSearcher(
        {
            "q1": [_result("https://a.com/1", "甲站", date="2025-01-01")],
            "q2": [_result("https://b.com/2", "乙站", date="2025-02-01")],
        }
    )
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=2),
        settings=_settings(),
        searcher=searcher,
        llm=_llm(),
        fetcher=FakeFetcher(ok_domains=("a.com", "b.com")),
    )

    assert EventType.done in _types(events)
    report = _payload(events, EventType.done)["report"]

    assert report["topic"] == "测试主题"
    assert report["title"] == "测试报告"
    assert report["executive_summary"]
    assert len(report["sections"]) == 2
    assert len(report["sources"]) == 2
    assert report["markdown"]

    # 四样交付物在 markdown 里都能找到
    for heading in (
        "## 摘要",
        "## 正文",
        "## 关键结论",
        "## 遗留问题",
        "## 来源列表",
        "## 研究过程记录",
        "## 置信度说明",
    ):
        assert heading in report["markdown"], f"报告缺少 {heading}"


@pytest.mark.asyncio
async def test_report_sources_match_real_urls():
    """**可追溯的落点**：报告里的每个 [Sn] 都能在来源表找到对应 URL。"""
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=FakeSearcher({"q1": [_result("https://a.com/1", "甲站")]}),
        llm=_llm(plan=_plan(1), reflect=_reflect(sufficient=True)),
        fetcher=FakeFetcher(ok_domains=("a.com",)),
    )

    report = _payload(events, EventType.done)["report"]
    sids = {s["sid"] for s in report["sources"]}
    assert sids == {"S1"}
    assert "[S1]" in report["markdown"]
    assert "https://a.com/1" in report["markdown"]
    # 正文里的编号不可能有来源表之外的
    assert [s["url"] for s in report["sources"]] == ["https://a.com/1"]


@pytest.mark.asyncio
async def test_event_order():
    """事件顺序即状态机形状 —— 顺序错了说明流程走歪了。"""
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=FakeSearcher({"q1": [_result("https://a.com/1", "甲站")]}),
        llm=_llm(plan=_plan(1)),
        fetcher=FakeFetcher(ok_domains=("a.com",)),
    )

    types = _types(events)
    assert types[0] is EventType.run_started
    assert types[-1] is EventType.done

    order = [
        EventType.plan,
        EventType.search_results,
        EventType.fetch,
        EventType.note,
        EventType.reflect,
        EventType.round_end,
        EventType.synthesize_start,
        EventType.section,
        EventType.report,
        EventType.done,
    ]
    positions = [types.index(t) for t in order if t in types]
    assert positions == sorted(positions), f"事件顺序错乱: {types}"


@pytest.mark.asyncio
async def test_seq_is_monotonic():
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=FakeSearcher({}),
        llm=_llm(plan=_plan(1)),
        fetcher=FakeFetcher(),
    )
    seqs = [e.seq for e in events]
    assert seqs == list(range(1, len(seqs) + 1))


# ══════════════════════════════════════════════════════════════
# 多轮迭代
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_second_round_uses_reflection_queries():
    """第二轮必须搜反思给出的新词，不是复读第一轮 —— 这是「深度研究」的底线。"""
    searcher = FakeSearcher(
        {
            "q1": [_result("https://a.com/1", "甲站")],
            "补充检索词": [_result("https://c.com/3", "丙站")],
        }
    )
    rounds = {"n": 0}

    def reflect():
        rounds["n"] += 1
        # 第一轮说不够并给出新词，第二轮说够了
        if rounds["n"] == 1:
            return _reflect(sufficient=False, next_queries=[{"qid": "q1", "query": "补充检索词"}])
        return _reflect(sufficient=True)

    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=3),
        settings=_settings(),
        searcher=searcher,
        llm=_llm(plan=_plan(1), reflect=reflect),
        fetcher=FakeFetcher(ok_domains=("a.com", "c.com")),
    )

    assert "补充检索词" in searcher.queries, f"第二轮没有用反思给的新词: {searcher.queries}"
    assert _payload(events, EventType.done)["rounds"] == 2
    # 反思给的新词必须与已用词明显不同 —— 这是 prompt 里的硬要求
    assert len(set(searcher.queries)) == len(searcher.queries)


@pytest.mark.asyncio
async def test_duplicate_query_skipped():
    """LLM 反复给近义检索词要被挡掉 —— 否则白烧检索配额。"""
    searcher = FakeSearcher({"人工智能现状": [_result("https://a.com/1", "甲站")]})

    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=2),
        settings=_settings(),
        searcher=searcher,
        llm=_llm(
            plan={
                "sub_questions": [{"text": "现状", "queries": ["人工智能现状"]}],
                "queries": ["人工智能现状"],
            },
            # 只改了个标点，归一化后与首轮词完全相同 —— 必须被拦下
            reflect=_reflect(
                sufficient=False, next_queries=[{"qid": "q1", "query": "人工智能现状！"}]
            ),
        ),
        fetcher=FakeFetcher(ok_domains=("a.com",)),
    )

    skipped = [e for e in events if e.type is EventType.search and e.payload.get("skipped")]
    assert skipped, "重复检索词没有被跳过"
    assert searcher.queries == ["人工智能现状"], f"重复词还是发出去了: {searcher.queries}"


# ══════════════════════════════════════════════════════════════
# 终止
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_max_rounds_reached():
    """反思永远说不够 → 撞轮次上限停下，不会无限循环。"""
    searcher = FakeSearcher(
        {f"q{i}": [_result(f"https://site{i}.com/x", f"站{i}")] for i in range(1, 4)}
    )

    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=2),
        settings=_settings(),
        searcher=searcher,
        llm=_llm(plan=_plan(2), reflect=_reflect(sufficient=False)),
        fetcher=FakeFetcher(ok_domains=("site1.com", "site2.com", "site3.com")),
    )

    stop = _payload(events, EventType.stop)
    assert stop["reason"] == "max_rounds_reached"
    assert _payload(events, EventType.done)["rounds"] == 2


@pytest.mark.asyncio
async def test_sufficient_stops_early():
    """材料确实够 → 第 1 轮就收，不硬凑轮次。"""
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=3),
        settings=_settings(),
        searcher=FakeSearcher({"q1": [_result("https://a.com/1", "甲站")]}),
        llm=_llm(plan=_plan(1), reflect=_reflect(sufficient=True)),
        fetcher=FakeFetcher(ok_domains=("a.com",)),
    )

    stop = _payload(events, EventType.stop)
    assert stop["reason"] == "sufficient"
    assert _payload(events, EventType.done)["rounds"] == 1


@pytest.mark.asyncio
async def test_llm_says_sufficient_but_coverage_low_keeps_going():
    """**LLM 单方面说够不算数** —— 覆盖率不达标就必须继续检索。

    LLM 有过早收手的已知偏差，程序必须能否决它。
    """
    searcher = FakeSearcher(
        {
            "q1": [_result("https://a.com/1", "甲站")],
            "q2": [_result("https://b.com/2", "乙站")],
            Q2: [_result("https://b.com/2", "乙站")],  # 兜底检索词就是子问题原文
        }
    )

    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=2),
        settings=_settings(),
        searcher=searcher,
        llm=_llm(
            # 反思说够了，但 reader 只覆盖了 q1 → q2 覆盖率为 0
            reader=_reader(qids=(1,)),
            reflect=_reflect(sufficient=True),
        ),
        fetcher=FakeFetcher(ok_domains=("a.com", "b.com")),
    )

    stop = _payload(events, EventType.stop)
    assert stop["reason"] != "sufficient", "覆盖率不达标却被判为 sufficient"
    assert _payload(events, EventType.done)["rounds"] == 2


@pytest.mark.asyncio
async def test_veto_falls_back_to_uncovered_question():
    """程序否决了 LLM，可它没给下一轮检索词 —— 得拿缺口子问题顶上，
    否则「程序否决权」只是句空话。"""
    searcher = FakeSearcher(
        {
            "q1": [_result("https://a.com/1", "甲站")],
            Q2: [_result("https://b.com/2", "乙站")],
        }
    )

    await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=2),
        settings=_settings(),
        searcher=searcher,
        llm=_llm(
            reader=_reader(qids=(1,)),  # q2 没材料
            reflect=_reflect(sufficient=True),  # 却又说够了、不给新词
        ),
        fetcher=FakeFetcher(ok_domains=("a.com", "b.com")),
    )

    assert Q2 in searcher.queries, f"没有拿缺口子问题兜底检索: {searcher.queries}"


@pytest.mark.asyncio
async def test_search_api_down_reports_starvation_not_stall():
    """**接口挂了不能被诊断成「这个主题没资料」。**

    两者会同时命中终止条件，但给用户的含义完全相反：一个是去查 API Key 和余额，
    一个是接受「查不到」。所以来源饥饿必须排在停滞之前。
    """
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=3),
        settings=_settings(),
        searcher=FakeSearcher(fail_all=True),
        llm=_llm(plan=_plan(1), reader={}, reflect=_reflect(sufficient=False)),
        fetcher=FakeFetcher(),
    )

    assert EventType.done in _types(events)
    stop = _payload(events, EventType.stop)
    assert stop["reason"] == "source_starvation"
    # 每一次失败都要发 error 事件，不能默默吞掉
    errors = [e for e in events if e.type is EventType.error and e.payload["scope"] == "search"]
    assert errors
    assert _payload(events, EventType.done)["report"]["markdown"]


@pytest.mark.asyncio
async def test_stall_detected():
    """连续多轮检索都搜不到新来源 → 判停滞，不空转到轮次上限。"""
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=5),
        settings=_settings(),
        searcher=FakeSearcher({}),  # 成功但零结果
        llm=_llm(
            plan=_plan(1),
            reader={},
            reflect=_reflect(sufficient=False),
        ),
        fetcher=FakeFetcher(),
    )

    assert _payload(events, EventType.stop)["reason"] == "no_new_sources"
    assert _payload(events, EventType.done)["rounds"] < 5


# ══════════════════════════════════════════════════════════════
# 收尾保护 —— 最差的情况下也得有报告
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_zero_sources_still_produces_report():
    """**不产出报告是更差的体验** —— 零来源也要出报告，全文标模型推断。"""
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=FakeSearcher({"q1": []}),
        llm=_llm(
            plan=_plan(1),
            reader={},
            reflect=_reflect(sufficient=True),
            head={
                "title": "空报告",
                "executive_summary": "没有材料。",
                "key_conclusions": [],
                "open_questions": ["未能获取来源"],
            },
        ),
        fetcher=FakeFetcher(),
    )

    report = _payload(events, EventType.done)["report"]
    assert report["markdown"]
    assert report["sources"] == []
    assert report["confidence"]["level"] == "low"
    assert report["confidence"]["overall_score"] == 0.0
    assert "（本次未能获取任何可用来源）" in report["markdown"]


@pytest.mark.asyncio
async def test_llm_entirely_down_still_produces_report():
    """LLM 全线挂掉 → 规划走模板、反思走兜底，报告照出。"""
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=FakeSearcher({"测试主题 的现状与基本事实": []}),
        llm=FakeLLM(),  # 所有阶段都抛 LLMError
        fetcher=FakeFetcher(),
    )

    plan_payload = _payload(events, EventType.plan)
    assert plan_payload["degraded"] is True
    assert plan_payload["sub_questions"]
    assert EventType.done in _types(events)
    assert _payload(events, EventType.done)["report"]["markdown"]


@pytest.mark.asyncio
async def test_sources_but_llm_down_does_not_claim_medium_confidence():
    """**真实踩过的坑**：检索抓取都成功、LLM 全挂 —— 报告是空的，但
    来源数量、域名多样性、抓取成功率全是满分，加权下来稳稳落在「中」。

    分数好看、报告没内容是这里最误导人的组合，必须被上限压到 low。
    """
    # 规划也失败了，检索词由模板兜底生成 —— 事先不可知，所以用 default
    searcher = FakeSearcher(
        default=[
            _result("https://a.com/1", "甲站"),
            _result("https://b.com/2", "乙站"),
            _result("https://c.com/3", "丙站"),
        ]
    )
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=searcher,
        llm=FakeLLM(),  # LLM 全线失败
        fetcher=FakeFetcher(ok_domains=("a.com", "b.com", "c.com")),
    )

    report = _payload(events, EventType.done)["report"]
    assert len(report["sources"]) == 3  # 来源侧一切正常
    assert report["key_conclusions"] == []  # 但一条结论都没有
    assert report["confidence"]["level"] == "low"
    assert any(
        "未能得出任何关键结论" in cap for cap in report["confidence"]["factors"]["caps_applied"]
    )
    # 正文也不能谎称「没搜到来源」—— 来源是有的，是读不出来
    assert "本次检索没有获取到与之相关的可用来源" not in report["markdown"]
    assert "已获取的来源里没能提取出可用材料" in report["markdown"]


@pytest.mark.asyncio
async def test_engine_error_does_not_escape():
    """依赖抛异常时引擎要吞掉并发出 fatal error 事件，不能把异常漏给调用方。"""

    class ExplodingSearcher:
        async def search(self, query, **kwargs):
            raise RuntimeError("检索客户端炸了")

        async def aclose(self):
            pass

    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=ExplodingSearcher(),
        llm=_llm(plan=_plan(1)),
        fetcher=FakeFetcher(),
    )

    errors = [e for e in events if e.type is EventType.error]
    assert errors and errors[-1].payload["fatal"] is True


# ══════════════════════════════════════════════════════════════
# 依赖归属
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_injected_deps_not_closed():
    """注入进来的依赖由调用方负责关闭 —— 引擎不能把别人的 client 关掉。"""
    searcher = FakeSearcher({})

    async for _ in run_research(
        ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=searcher,
        llm=_llm(plan=_plan(1)),
        fetcher=FakeFetcher(),
    ):
        pass

    assert searcher.closed is False


# ══════════════════════════════════════════════════════════════
# 抓取降级
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_blocked_source_degrades_to_summary():
    """反爬站点降级用搜索摘要，来源仍进列表并如实标来路。"""
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=FakeSearcher({"q1": [_result("https://mp.weixin.qq.com/s/abc", "公众号文章")]}),
        llm=_llm(plan=_plan(1)),
        fetcher=FakeFetcher(blocked_domains=("mp.weixin.qq.com",)),
    )

    failed = [e for e in events if e.type is EventType.fetch_failed]
    assert failed, "黑名单来源没有发 fetch_failed 事件"
    assert failed[0].payload["status"] == "blocked"
    assert failed[0].payload["fallback"] == "search_summary"
    assert failed[0].payload["usable"] is True

    sources = _payload(events, EventType.done)["report"]["sources"]
    assert sources[0]["content_origin"] == "search_summary"
    assert sources[0]["fetch_status"] == "blocked"


@pytest.mark.asyncio
async def test_extract_failed_is_not_reported_as_success():
    """HTTP 200 但没抽出正文 → 必须报 extract_failed，不能粉饰成抓取成功。"""
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=FakeSearcher({"q1": [_result("https://c.com/9", "模板页")]}),
        llm=_llm(plan=_plan(1)),
        fetcher=FakeFetcher(),  # 不在 ok_domains 里 → extract_failed
    )

    assert EventType.fetch not in _types(events)
    failed = [e for e in events if e.type is EventType.fetch_failed]
    assert failed and failed[0].payload["status"] == "extract_failed"

    # 抓取成功率因子不该把这次「什么都没读到」算成成功
    report = _payload(events, EventType.done)["report"]
    assert report["confidence"]["factors"]["raw"]["fetch_ok"] == 0


@pytest.mark.asyncio
async def test_no_fetch_mode_skips_fetching():
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1, fetch_enabled=False),
        settings=_settings(),
        searcher=FakeSearcher({"q1": [_result("https://a.com/1", "甲站")]}),
        llm=_llm(plan=_plan(1)),
        fetcher=FakeFetcher(ok_domains=("a.com",)),
    )

    assert EventType.fetch not in _types(events)
    assert EventType.done in _types(events)


# ══════════════════════════════════════════════════════════════
# 去重
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_duplicate_url_only_registered_once():
    """同一篇文章的两个 URL 变体只占一个 sid，但两个检索词都记在 found_by_queries。"""
    events = await _collect(
        request=ResearchRequest(topic="测试主题", max_rounds=1),
        settings=_settings(),
        searcher=FakeSearcher(
            {
                "q1": [
                    _result("https://a.com/1?utm_source=wechat", "甲站"),
                    _result("https://a.com/1", "甲站"),
                ]
            }
        ),
        llm=_llm(plan=_plan(1)),
        fetcher=FakeFetcher(ok_domains=("a.com",)),
    )

    report = _payload(events, EventType.done)["report"]
    assert len(report["sources"]) == 1
    assert report["sources"][0]["sid"] == "S1"
