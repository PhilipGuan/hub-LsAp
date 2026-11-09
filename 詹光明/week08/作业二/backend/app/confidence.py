"""置信度 —— 纯代码算出来的，不是 LLM 说的。

让模型给自己打分是自欺欺人：它既没有来源计数，也不知道有几条结论没引用。
所以这里七项因子全部由程序算，LLM **只负责写定性的局限说明**，不参与打分。

除了加权求和，还叠一层**硬上限**：来源太少、域名太集中、无来源结论太多、
真读到的网页太少、抓取大面积失败 —— 任何一条踩中，总分就被按到上限以下。
加权平均容易被「其它项都很高」拉回来，硬上限不会。
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime

from .config import Settings
from .config import settings as default_settings
from .models import (
    Conclusion,
    ConfidenceFactors,
    ConfidenceReport,
    ContentOrigin,
    FetchStatus,
    Source,
)

logger = logging.getLogger(__name__)

# 七项因子的权重，和为 1
FACTOR_WEIGHTS: dict[str, float] = {
    "source_count": 0.22,
    "domain_diversity": 0.18,
    "authority": 0.18,
    "recency": 0.14,
    "corroboration": 0.18,
    "fetch_success": 0.06,
    "citation_coverage": 0.04,
}

# 权威域名后缀 —— 政府 / 教育 / 科研 / 非营利
_AUTHORITATIVE_SUFFIXES = (
    ".gov.cn",
    ".gov",
    ".edu.cn",
    ".edu",
    ".ac.cn",
    ".org.cn",
    ".org",
)

# 站点名里出现这些词，认为是一手或准一手来源
_PRIMARY_KEYWORDS = ("官方", "政府", "大学", "学院", "研究院", "研究所", "统计局", "协会", "白皮书")
# 媒体类：比随手转载强，但不如一手
_MEDIA_KEYWORDS = ("新闻", "新华", "人民", "央视", "日报", "晚报", "周刊", "财经", "网")

_LEVEL_HIGH = 0.75
_LEVEL_MEDIUM = 0.50

# ── 硬上限 ────────────────────────────────────────────────────
CAP_MIN_SOURCES = 0.35
CAP_MIN_DOMAINS = 0.45
CAP_UNSOURCED_RATIO = 0.50
CAP_PAGE_ORIGIN_RATIO = 0.50
CAP_FETCH_SUCCESS = 0.60
# 一条结论都没有 —— 这份报告等于没有产出，不能因为「来源挺多」就给中等可信度
CAP_NO_CONCLUSIONS = 0.30

MIN_SOURCES = 3
MIN_DOMAINS = 2
MAX_UNSOURCED_RATIO = 0.30
MIN_PAGE_ORIGIN_RATIO = 0.30
MIN_FETCH_SUCCESS = 0.40

# 日期缺失超过这个比例，时效因子封顶
MAX_MISSING_DATE_RATIO = 0.60
MISSING_DATE_CAP = 0.40


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _score_source_count(n: int) -> float:
    return _clamp(min(n, 10) / 10)


def _score_domain_diversity(distinct_domains: int) -> float:
    if distinct_domains >= 6:
        return 1.0
    if distinct_domains >= 4:
        return 0.8
    if distinct_domains >= 2:
        return 0.5
    return 0.0


def _score_one_authority(source: Source) -> float:
    domain = (source.domain or "").lower()
    site = source.site_name or ""

    if any(domain.endswith(suffix) for suffix in _AUTHORITATIVE_SUFFIXES):
        return 1.0
    if any(keyword in site for keyword in _PRIMARY_KEYWORDS):
        return 1.0
    if any(keyword in site for keyword in _MEDIA_KEYWORDS):
        return 0.7
    return 0.3


def _score_recency(sources: list[Source], now: datetime, window_days: int) -> tuple[float, float]:
    """返回 (时效分, 日期缺失比例)。"""
    dates = [s.published_at for s in sources if s.published_at]
    missing_ratio = 1.0 - len(dates) / len(sources) if sources else 1.0

    if not dates:
        return 0.0, missing_ratio

    reference = now
    ages = []
    for date in dates:
        # 有的来源只给了日期没给时区，naive 与 aware 相减会直接抛异常
        if (date.tzinfo is None) != (reference.tzinfo is None):
            date = date.replace(tzinfo=None) if reference.tzinfo is None else date.astimezone()
        ages.append(max(0.0, (reference - date).total_seconds() / 86400))

    median_age = statistics.median(ages)
    score = _clamp(1.0 - median_age / window_days) if window_days > 0 else 0.0

    if missing_ratio > MAX_MISSING_DATE_RATIO:
        score = min(score, MISSING_DATE_CAP)

    return score, missing_ratio


def _score_corroboration(conclusions: list[Conclusion], sid_to_domain: dict[str, str]) -> float:
    """每条结论的支撑独立域名数：1 个→0 分，2 个→0.6，≥3 个→1，再取平均。"""
    if not conclusions:
        return 0.0

    scores: list[float] = []
    for conclusion in conclusions:
        domains = {sid_to_domain[sid] for sid in conclusion.sids if sid in sid_to_domain}
        if len(domains) >= 3:
            scores.append(1.0)
        elif len(domains) == 2:
            scores.append(0.6)
        else:
            scores.append(0.0)

    return sum(scores) / len(scores)


def _score_citation_coverage(cited: int, total: int) -> float:
    """被引用的来源占比，到 50% 就算满分 —— 要求每条来源都被引用并不现实。"""
    if total <= 0:
        return 0.0
    return _clamp((cited / total) / 0.5)


def _fetch_stats(sources: list[Source]) -> tuple[float, int, int]:
    """返回 (抓取成功率, 尝试数, 成功数)。未抓取记 0，不许当成功。"""
    attempted = [s for s in sources if s.fetched]
    if not attempted:
        return 0.0, 0, 0
    ok = sum(1 for s in attempted if s.fetch_status is FetchStatus.ok)
    return ok / len(attempted), len(attempted), ok


def compute_confidence(
    sources: list[Source],
    conclusions: list[Conclusion] | None = None,
    *,
    cited_sids: set[str] | None = None,
    unsourced_conclusion_count: int = 0,
    total_conclusion_count: int = 0,
    inferred_statements: list[str] | None = None,
    limitations: str = "",
    now: datetime | None = None,
    settings: Settings | None = None,
) -> ConfidenceReport:
    """按七项因子加权算分，再叠硬上限。

    ``sources`` 传**全部**来源（含降级与判重的）—— 判重与 ``readable`` 过滤
    在这里做，因为「抓取成功率」的分母是「尝试抓过的」，跟能不能引用无关。
    """
    cfg = settings or default_settings
    now = now or datetime.now()

    readable = [s for s in sources if s.readable]
    n = len(readable)

    sid_to_domain = {s.sid: (s.domain or s.url) for s in readable}
    distinct_domains = len({(s.domain or s.url) for s in readable})

    cited_sids = cited_sids or set()
    cited_readable = len({s.sid for s in readable if s.sid in cited_sids})

    factors = ConfidenceFactors(weights=dict(FACTOR_WEIGHTS))

    page_count = sum(1 for s in readable if s.content_origin is ContentOrigin.page)
    page_ratio = page_count / n if n else 0.0

    recency_score, missing_date_ratio = _score_recency(readable, now, cfg.freshness_window_days)
    fetch_success, attempted, fetch_ok = _fetch_stats(sources)

    factors.source_count = _score_source_count(n)
    factors.domain_diversity = _score_domain_diversity(distinct_domains)
    factors.authority = sum(_score_one_authority(s) for s in readable) / n if n else 0.0
    factors.recency = recency_score
    factors.corroboration = _score_corroboration(conclusions or [], sid_to_domain)
    factors.fetch_success = fetch_success
    factors.citation_coverage = _score_citation_coverage(cited_readable, n)

    score = sum(getattr(factors, name) * weight for name, weight in FACTOR_WEIGHTS.items())
    if not n:
        # 一条可用来源都没有 —— 其余因子再好看也不该有分数
        score = 0.0

    unsourced_ratio = (
        unsourced_conclusion_count / total_conclusion_count if total_conclusion_count else 0.0
    )

    caps: list[str] = []
    if n == 0:
        caps.append("没有任何可用来源，所有结论均为模型推断")
        score = 0.0
    else:
        # 有来源不等于有结论。阅读 / 综合阶段整体失败时来源照样是满的，
        # 但这份报告实际什么都没答出来 —— 那种情况下「来源挺多」是假象，
        # 不给这条上限的话它会稳稳落在中等区间，正好是最误导人的位置。
        if total_conclusion_count == 0:
            caps.append(
                f"本次未能得出任何关键结论，总分封顶 {CAP_NO_CONCLUSIONS}（来源数量不能替代结论）"
            )
            score = min(score, CAP_NO_CONCLUSIONS)
        if n < MIN_SOURCES:
            caps.append(f"可用来源仅 {n} 个（少于 {MIN_SOURCES} 个），总分封顶 {CAP_MIN_SOURCES}")
            score = min(score, CAP_MIN_SOURCES)
        if distinct_domains < MIN_DOMAINS:
            caps.append(
                f"来源集中在 {distinct_domains} 个独立域名（少于 {MIN_DOMAINS} 个），"
                f"总分封顶 {CAP_MIN_DOMAINS}"
            )
            score = min(score, CAP_MIN_DOMAINS)
        if unsourced_ratio > MAX_UNSOURCED_RATIO:
            caps.append(
                f"无来源结论占比 {unsourced_ratio:.0%}（超过 {MAX_UNSOURCED_RATIO:.0%}），"
                f"总分封顶 {CAP_UNSOURCED_RATIO}"
            )
            score = min(score, CAP_UNSOURCED_RATIO)
        if page_ratio < MIN_PAGE_ORIGIN_RATIO:
            caps.append(
                f"真正读到网页正文的来源仅占 {page_ratio:.0%}"
                f"（低于 {MIN_PAGE_ORIGIN_RATIO:.0%}），总分封顶 {CAP_PAGE_ORIGIN_RATIO}"
            )
            score = min(score, CAP_PAGE_ORIGIN_RATIO)
        if fetch_success < MIN_FETCH_SUCCESS:
            caps.append(
                f"抓取成功率仅 {fetch_success:.0%}（低于 {MIN_FETCH_SUCCESS:.0%}），"
                f"总分封顶 {CAP_FETCH_SUCCESS}"
            )
            score = min(score, CAP_FETCH_SUCCESS)

    score = _clamp(score)
    level = "high" if score >= _LEVEL_HIGH else "medium" if score >= _LEVEL_MEDIUM else "low"

    factors.caps_applied = caps
    factors.raw = {
        "readable_sources": n,
        "total_sources": len(sources),
        "distinct_domains": distinct_domains,
        "page_origin_sources": page_count,
        "page_origin_ratio": round(page_ratio, 4),
        "fetch_attempted": attempted,
        "fetch_ok": fetch_ok,
        "missing_date_ratio": round(missing_date_ratio, 4),
        "cited_readable_sources": cited_readable,
        "unsourced_ratio": round(unsourced_ratio, 4),
    }

    latest_date = max((s.published_at for s in readable if s.published_at), default=None)

    return ConfidenceReport(
        overall_score=round(score, 4),
        level=level,
        factors=factors,
        unsourced_conclusion_count=unsourced_conclusion_count,
        total_conclusion_count=total_conclusion_count,
        unsourced_ratio=round(unsourced_ratio, 4),
        inferred_statements=inferred_statements or [],
        limitations=limitations,
        as_of=latest_date,
        latest_source_date=latest_date,
    )


__all__ = [
    "CAP_MIN_DOMAINS",
    "CAP_MIN_SOURCES",
    "CAP_NO_CONCLUSIONS",
    "CAP_PAGE_ORIGIN_RATIO",
    "CAP_UNSOURCED_RATIO",
    "FACTOR_WEIGHTS",
    "MAX_UNSOURCED_RATIO",
    "MIN_DOMAINS",
    "MIN_SOURCES",
    "compute_confidence",
]
