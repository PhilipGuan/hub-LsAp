"""置信度打分与硬上限。

阶段 5 的验收：构造单来源 state 验证 low + 硬上限生效。

硬上限是这一层存在的理由 —— 加权平均会被「其它项都很好」拉回来，
上限不会。所以每个上限都要有专门把「其它项拉满」的用例。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.confidence import (
    CAP_FETCH_SUCCESS,
    CAP_MIN_DOMAINS,
    CAP_MIN_SOURCES,
    CAP_NO_CONCLUSIONS,
    CAP_PAGE_ORIGIN_RATIO,
    CAP_UNSOURCED_RATIO,
    FACTOR_WEIGHTS,
    compute_confidence,
)
from app.models import Conclusion, ContentOrigin, FetchStatus, Source

NOW = datetime(2025, 6, 1)


def _source(
    sid: str,
    *,
    domain: str | None = None,
    origin: ContentOrigin = ContentOrigin.page,
    status: FetchStatus = FetchStatus.ok,
    fetched: bool = True,
    site_name: str = "某站",
    days_old: int | None = 10,
    duplicate_of: str | None = None,
) -> Source:
    return Source(
        sid=sid,
        url=f"https://{domain or sid.lower()}.example.com/a",
        normalized_url=f"https://{domain or sid.lower()}.example.com/a",
        title=f"标题 {sid}",
        site_name=site_name,
        domain=domain or f"{sid.lower()}.example.com",
        content="正文" * 100,
        content_chars=200,
        content_origin=origin,
        fetch_status=status,
        fetched=fetched,
        duplicate_of=duplicate_of,
        published_at=None if days_old is None else NOW - timedelta(days=days_old),
    )


def _many(n: int, **kwargs) -> list[Source]:
    """造 n 条来源，每个都是独立域名。"""
    return [_source(f"S{i}", domain=f"site{i}.com", **kwargs) for i in range(1, n + 1)]


class TestWeights:
    def test_weights_sum_to_one(self) -> None:
        assert abs(sum(FACTOR_WEIGHTS.values()) - 1.0) < 1e-9


class TestHealthyRun:
    def test_rich_sources_score_high(self) -> None:
        sources = _many(8)
        conclusions = [
            Conclusion(text=f"结论 {i}", sids=[f"S{i}", f"S{i + 1}", f"S{i + 2}"])
            for i in range(1, 6)
        ]
        report = compute_confidence(
            sources,
            conclusions,
            cited_sids={s.sid for s in sources[:6]},
            total_conclusion_count=5,
            now=NOW,
        )

        assert report.level == "high"
        assert report.factors.caps_applied == []
        assert report.factors.domain_diversity == 1.0
        assert report.factors.source_count == 0.8

    def test_score_matches_manual_weighted_sum(self) -> None:
        """factors.raw 里的计数要能按公式手算回 overall_score。"""
        sources = _many(5)
        conclusions = [Conclusion(text="结论", sids=["S1", "S2", "S3"])]
        report = compute_confidence(
            sources,
            conclusions,
            cited_sids={"S1", "S2"},
            total_conclusion_count=1,
            now=NOW,
        )

        factors = report.factors
        expected = sum(getattr(factors, name) * w for name, w in FACTOR_WEIGHTS.items())

        assert abs(report.overall_score - min(expected, 1.0)) < 1e-4


class TestHardCaps:
    def test_single_source_capped(self) -> None:
        """单来源 —— 阶段 5 的验收用例。"""
        report = compute_confidence([_source("S1")], [], cited_sids={"S1"}, now=NOW)

        assert report.level == "low"
        assert report.overall_score <= CAP_MIN_SOURCES
        assert any("少于 3 个" in cap for cap in report.factors.caps_applied)

    def test_two_sources_still_capped(self) -> None:
        report = compute_confidence(_many(2), [], now=NOW)

        assert report.overall_score <= CAP_MIN_SOURCES

    def test_min_sources_cap_survives_perfect_other_factors(self) -> None:
        """其它因子全部拉满，来源数不够仍然封顶 —— 这正是硬上限的意义。"""
        sources = _many(2)
        report = compute_confidence(
            sources,
            [],
            cited_sids={s.sid for s in sources},
            now=NOW,
        )

        # 引用覆盖率、时效、抓取成功率都是满分，但来源数不够
        assert report.factors.citation_coverage == 1.0
        assert report.factors.fetch_success == 1.0
        assert report.overall_score <= CAP_MIN_SOURCES

    def test_same_domain_capped(self) -> None:
        """来源够多但全在一个域名下 —— 仍是单一信源。"""
        sources = [_source(f"S{i}", domain="same.com") for i in range(1, 6)]
        report = compute_confidence(sources, [], now=NOW)

        assert report.factors.domain_diversity == 0.0
        assert report.overall_score <= CAP_MIN_DOMAINS
        assert any("独立域名" in cap for cap in report.factors.caps_applied)

    def test_unsourced_conclusions_capped(self) -> None:
        sources = _many(8)
        report = compute_confidence(
            sources,
            [],
            total_conclusion_count=10,
            unsourced_conclusion_count=5,  # 50% > 30%
            now=NOW,
        )

        assert report.overall_score <= CAP_UNSOURCED_RATIO
        assert any("无来源结论占比" in cap for cap in report.factors.caps_applied)

    def test_low_page_origin_ratio_capped(self) -> None:
        """全靠搜索摘要拼出来的报告，不该有好分数。"""
        sources = [
            _source(f"S{i}", domain=f"site{i}.com", origin=ContentOrigin.search_summary)
            for i in range(1, 9)
        ]
        report = compute_confidence(sources, [], now=NOW)

        assert report.factors.raw["page_origin_ratio"] == 0.0
        assert report.overall_score <= CAP_PAGE_ORIGIN_RATIO
        assert any("真正读到网页正文" in cap for cap in report.factors.caps_applied)

    def test_fetch_failure_capped(self) -> None:
        sources = [
            _source(
                f"S{i}",
                domain=f"site{i}.com",
                status=FetchStatus.timeout if i > 2 else FetchStatus.ok,
            )
            for i in range(1, 9)
        ]
        report = compute_confidence(sources, [], now=NOW)

        assert report.factors.fetch_success < 0.4
        assert report.overall_score <= CAP_FETCH_SUCCESS
        assert any("抓取成功率" in cap for cap in report.factors.caps_applied)

    def test_multiple_caps_all_recorded(self) -> None:
        """多个上限同时踩中 —— 每一条都要记下来，用户才知道分是怎么被压下来的。"""
        sources = [
            _source(f"S{i}", domain="same.com", origin=ContentOrigin.search_summary)
            for i in range(1, 3)
        ]
        report = compute_confidence(
            sources,
            [],
            total_conclusion_count=4,
            unsourced_conclusion_count=4,  # 100% > 30%
            now=NOW,
        )

        # 来源只有 2 个 + 全在同一域名 + 一条网页正文都没读到 + 无来源结论超标
        assert len(report.factors.caps_applied) == 4

    def test_zero_readable_sources_short_circuits(self) -> None:
        """一条可用来源都没有时只记一条上限 —— 其余上限此时没有意义。"""
        report = compute_confidence(
            [_source("S1", origin=ContentOrigin.snippet)],
            [],
            total_conclusion_count=4,
            unsourced_conclusion_count=4,
            now=NOW,
        )

        assert len(report.factors.caps_applied) == 1


class TestNoConclusions:
    """**来源多不等于报告有内容。**

    阅读 / 综合阶段整体失败时，来源侧的各项因子照样是满的，但这份报告
    实际一条结论都没答出来。不加这条上限，它就会稳稳落在「中」——
    正好是最误导人的位置：分数看起来尚可，报告却是空的。
    """

    def test_many_sources_but_no_conclusions_caps_low(self) -> None:
        report = compute_confidence(_many(10), [], total_conclusion_count=0, now=NOW)

        assert report.overall_score <= CAP_NO_CONCLUSIONS
        assert report.level == "low"
        assert any("未能得出任何关键结论" in cap for cap in report.factors.caps_applied)

    def test_unsourced_ratio_zero_does_not_mask_it(self) -> None:
        """0 条无来源结论 / 0 条结论 —— 占比算出来是 0%，但那不是「全都来源充足」。"""
        report = compute_confidence(
            [], [], unsourced_conclusion_count=0, total_conclusion_count=0, now=NOW
        )

        assert report.unsourced_ratio == 0.0
        assert report.level == "low"

    def test_conclusions_present_lifts_the_cap(self) -> None:
        """有结论时这条上限不该再压分 —— 它是「没产出」的专属上限。"""
        conclusions = [
            Conclusion(text=f"结论{i}", sids=[f"S{i}", f"S{i + 1}", f"S{i + 2}"])
            for i in range(1, 4)
        ]
        report = compute_confidence(_many(10), conclusions, total_conclusion_count=3, now=NOW)

        assert not any("未能得出任何关键结论" in cap for cap in report.factors.caps_applied)
        assert report.overall_score > CAP_NO_CONCLUSIONS


class TestEmptyAndDegraded:
    def test_no_sources_at_all(self) -> None:
        report = compute_confidence([], [], now=NOW)

        assert report.overall_score == 0.0
        assert report.level == "low"
        assert any("没有任何可用来源" in cap for cap in report.factors.caps_applied)

    def test_all_duplicates_counts_as_no_sources(self) -> None:
        sources = [_source(f"S{i}", duplicate_of="S1") for i in range(1, 5)]
        report = compute_confidence(sources, [], now=NOW)

        assert report.factors.raw["readable_sources"] == 0
        assert report.overall_score == 0.0

    def test_snippet_only_sources_are_not_readable(self) -> None:
        """只有一行搜索描述的来源不配当引用依据。"""
        sources = [
            _source(f"S{i}", domain=f"s{i}.com", origin=ContentOrigin.snippet) for i in range(4)
        ]
        report = compute_confidence(sources, [], now=NOW)

        assert report.factors.raw["readable_sources"] == 0

    def test_never_fetched_counts_as_failure(self) -> None:
        """未抓取记 0，不许当成功。"""
        sources = _many(5, fetched=False, status=FetchStatus.skipped)
        report = compute_confidence(sources, [], now=NOW)

        assert report.factors.fetch_success == 0.0
        assert report.factors.raw["fetch_attempted"] == 0


class TestRecency:
    def test_fresh_sources_score_high(self) -> None:
        report = compute_confidence(_many(5, days_old=5), [], now=NOW)

        assert report.factors.recency > 0.9

    def test_stale_sources_score_low(self) -> None:
        report = compute_confidence(_many(5, days_old=350), [], now=NOW)

        assert report.factors.recency < 0.1

    def test_missing_dates_capped(self) -> None:
        report = compute_confidence(_many(5, days_old=None), [], now=NOW)

        assert report.factors.recency == 0.0
        assert report.factors.raw["missing_date_ratio"] == 1.0

    def test_as_of_is_latest_source_date(self) -> None:
        sources = [
            _source("S1", domain="a.com", days_old=100),
            _source("S2", domain="b.com", days_old=3),
        ]
        report = compute_confidence(sources, [], now=NOW)

        assert report.as_of == NOW - timedelta(days=3)


class TestAuthority:
    def test_gov_domain_scores_highest(self) -> None:
        sources = [_source("S1", domain="www.gov.cn")]
        report = compute_confidence(sources, [], now=NOW)

        assert report.factors.authority == 1.0

    def test_media_scores_middle(self) -> None:
        sources = [_source("S1", domain="news.com", site_name="某新闻")]
        report = compute_confidence(sources, [], now=NOW)

        assert report.factors.authority == 0.7

    def test_unknown_site_scores_lowest(self) -> None:
        sources = [_source("S1", domain="random.com", site_name="随便一个站")]
        report = compute_confidence(sources, [], now=NOW)

        assert report.factors.authority == 0.3


class TestCorroboration:
    def test_three_domains_full_score(self) -> None:
        sources = _many(3)
        report = compute_confidence(
            sources, [Conclusion(text="结论", sids=["S1", "S2", "S3"])], now=NOW
        )

        assert report.factors.corroboration == 1.0

    def test_two_domains_partial(self) -> None:
        sources = _many(3)
        report = compute_confidence(sources, [Conclusion(text="结论", sids=["S1", "S2"])], now=NOW)

        assert report.factors.corroboration == 0.6

    def test_single_domain_zero(self) -> None:
        sources = _many(3)
        report = compute_confidence(sources, [Conclusion(text="结论", sids=["S1"])], now=NOW)

        assert report.factors.corroboration == 0.0

    def test_same_domain_twice_is_still_one_domain(self) -> None:
        """两条来源同域名 = 一个信源，不能算互相佐证。"""
        sources = [_source("S1", domain="same.com"), _source("S2", domain="same.com")]
        report = compute_confidence(sources, [Conclusion(text="结论", sids=["S1", "S2"])], now=NOW)

        assert report.factors.corroboration == 0.0


class TestCitationCoverage:
    def test_half_cited_is_full_marks(self) -> None:
        sources = _many(4)
        report = compute_confidence(sources, [], cited_sids={"S1", "S2"}, now=NOW)

        assert report.factors.citation_coverage == 1.0

    def test_none_cited_is_zero(self) -> None:
        report = compute_confidence(_many(4), [], cited_sids=set(), now=NOW)

        assert report.factors.citation_coverage == 0.0

    def test_orphan_sources_counted(self) -> None:
        sources = _many(4)
        report = compute_confidence(sources, [], cited_sids={"S1"}, now=NOW)

        assert report.factors.raw["cited_readable_sources"] == 1
