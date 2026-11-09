"""全部 Pydantic 数据契约。

这里只放纯数据结构，不放业务逻辑 —— 各模块之间的接口以本文件的模型为准。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ══════════════════════════════════════════════════════════════
# 枚举
# ══════════════════════════════════════════════════════════════


class FetchStatus(StrEnum):
    """网页抓取的结果状态。"""

    ok = "ok"
    http_error = "http_error"
    timeout = "timeout"
    too_large = "too_large"
    blocked = "blocked"  # 在黑名单里，主动放弃
    unsupported_type = "unsupported_type"  # 不是 HTML
    connection_error = "connection_error"
    # 网页下下来了，但一条正文都没抽出来（模板页 / 纯 JS 渲染页）
    extract_failed = "extract_failed"
    skipped = "skipped"  # 未尝试抓取


class ContentOrigin(StrEnum):
    """来源正文的真实来源 —— 区分「真读到网页」还是「降级用搜索摘要」。

    这个字段既参与置信度打分，也是验收「是不是真做了深度研究」的依据。
    """

    page = "page"
    search_summary = "search_summary"
    snippet = "snippet"


class RunStatus(StrEnum):
    init = "init"
    planning = "planning"
    searching = "searching"
    fetching = "fetching"
    reading = "reading"
    reflecting = "reflecting"
    synthesizing = "synthesizing"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class StopReason(StrEnum):
    sufficient = "sufficient"
    max_rounds_reached = "max_rounds_reached"
    no_new_sources = "no_new_sources"
    budget_exceeded = "budget_exceeded"
    timeout = "timeout"
    source_starvation = "source_starvation"
    cancelled = "cancelled"
    error = "error"


# ══════════════════════════════════════════════════════════════
# 请求
# ══════════════════════════════════════════════════════════════


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=300)
    language: str = "zh"
    max_rounds: int = Field(3, ge=1, le=6)
    fetch_enabled: bool = True


# ══════════════════════════════════════════════════════════════
# 检索
# ══════════════════════════════════════════════════════════════


class RawSearchResult(BaseModel):
    """博查返回的单条结果，字段已归一化命名。

    博查的响应结构未经官方一手确认，解析层做了多路径与多字段名容错，
    这里保留字段名映射后的结果。
    """

    title: str = ""
    url: str = ""
    display_url: str = ""
    snippet: str = ""
    summary: str = ""
    site_name: str = ""
    published_at: datetime | None = None
    date_last_crawled: datetime | None = None


class SearchOutcome(BaseModel):
    """一次检索调用的完整结果，含诊断信息。"""

    query: str
    ok: bool
    results: list[RawSearchResult] = Field(default_factory=list)
    # 实际命中的 JSON 路径，例如 "data.webPages.value"；用于核对博查响应结构
    matched_path: str | None = None
    code: int | None = None
    msg: str | None = None
    log_id: str | None = None
    total_estimated_matches: int | None = None
    latency_ms: int = 0
    attempts: int = 1
    error: str | None = None
    # 诊断用：解析出 0 条时靠这两个字段核对博查的真实响应结构
    top_level_keys: list[str] = Field(default_factory=list)
    raw: dict[str, Any] | None = None


class QueryRecord(BaseModel):
    query_id: str
    qid: str = ""
    text: str
    round: int
    reason: Literal["initial_plan", "reflect_gap", "retry", "skipped_duplicate"] = "initial_plan"
    ok: bool = True
    code: int | None = None
    error: str | None = None
    result_count: int = 0
    new_source_count: int = 0
    latency_ms: int = 0


# ══════════════════════════════════════════════════════════════
# 来源
# ══════════════════════════════════════════════════════════════


class Source(BaseModel):
    """一条来源。sid 在检索去重通过时立即分配，全局唯一、永不复用、永不重排。"""

    sid: str
    url: str
    normalized_url: str
    title: str = ""
    site_name: str = ""
    domain: str = ""
    snippet: str = ""
    search_summary: str | None = None
    published_at: datetime | None = None

    first_seen_round: int = 0
    found_by_queries: list[str] = Field(default_factory=list)

    # 抓取
    fetched: bool = False
    fetch_status: FetchStatus = FetchStatus.skipped
    http_status: int | None = None
    fetch_error: str | None = None

    # 正文
    content: str | None = None
    content_chars: int = 0
    content_hash: str | None = None
    extractor: str | None = None
    content_origin: ContentOrigin = ContentOrigin.snippet

    # 去重
    duplicate_of: str | None = None

    @property
    def readable(self) -> bool:
        """能否作为引用依据：抓到网页正文，或至少有搜索摘要兜底。"""
        if self.duplicate_of:
            return False
        return self.content_origin in (ContentOrigin.page, ContentOrigin.search_summary)


# ══════════════════════════════════════════════════════════════
# 笔记 / 子问题 / 反思
# ══════════════════════════════════════════════════════════════


class Note(BaseModel):
    """reader 从单个来源里抽出的一条事实主张。"""

    note_id: str
    sid: str
    qid: str = ""
    claim: str
    evidence: str = ""
    quote: str | None = None
    strength: float = Field(0.5, ge=0, le=1)
    weak: bool = False  # 正文降级到 snippet 时标记
    round: int = 0


class SubQuestion(BaseModel):
    qid: str
    text: str
    rationale: str = ""
    initial_queries: list[str] = Field(default_factory=list)
    status: Literal["pending", "in_progress", "covered", "insufficient"] = "pending"
    added_round: int = 0

    # 程序化计算的覆盖率 0~1（不信任 LLM 自评）
    coverage: float = 0.0


class ReflectionRecord(BaseModel):
    round: int
    llm_sufficient: bool
    decision: Literal["continue", "stop"]
    stop_reason: str | None = None
    coverage: dict[str, float] = Field(default_factory=dict)
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    next_queries: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str = ""
    new_valid_sources: int = 0


# ══════════════════════════════════════════════════════════════
# 置信度
# ══════════════════════════════════════════════════════════════


class ConfidenceFactors(BaseModel):
    source_count: float = 0.0
    domain_diversity: float = 0.0
    authority: float = 0.0
    recency: float = 0.0
    corroboration: float = 0.0
    fetch_success: float = 0.0
    citation_coverage: float = 0.0
    weights: dict[str, float] = Field(default_factory=dict)
    # 原始计数，前端/报告里用来解释「为什么是这个分」
    raw: dict[str, Any] = Field(default_factory=dict)
    caps_applied: list[str] = Field(default_factory=list)


class ConfidenceReport(BaseModel):
    overall_score: float = Field(0.0, ge=0, le=1)
    level: Literal["high", "medium", "low"] = "low"
    factors: ConfidenceFactors = Field(default_factory=ConfidenceFactors)
    unsourced_conclusion_count: int = 0
    total_conclusion_count: int = 0
    unsourced_ratio: float = 0.0
    inferred_statements: list[str] = Field(default_factory=list)
    limitations: str = ""
    as_of: datetime | None = None
    latest_source_date: datetime | None = None


# ══════════════════════════════════════════════════════════════
# 报告
# ══════════════════════════════════════════════════════════════


class Conclusion(BaseModel):
    text: str
    sids: list[str] = Field(default_factory=list)
    basis: Literal["sourced", "inferred"] = "sourced"
    confidence: float = Field(0.5, ge=0, le=1)
    domain_count: int = 0


class ReportSection(BaseModel):
    heading: str
    body_md: str
    qid: str | None = None
    citation_sids: list[str] = Field(default_factory=list)


class SourceRef(BaseModel):
    """报告来源列表里的一条 —— 与 Source 的区别是它带「被引用情况」。"""

    sid: str
    title: str
    url: str
    site_name: str = ""
    domain: str = ""
    published_at: datetime | None = None
    fetched: bool = False
    fetch_status: FetchStatus = FetchStatus.skipped
    content_origin: ContentOrigin = ContentOrigin.snippet
    # 抽到几条笔记。用来把「读了但没引用」和「压根没读出来」分开 ——
    # 两者在来源表里都显示成「未引用」，但一个是选择，一个是失败。
    # ``readable`` 则区分「读了但抽不出」和「根本没送去读」（重复来源 /
    # 只剩片段），三种情况的 0 条笔记含义完全不同。
    note_count: int = 0
    readable: bool = False
    cited: bool = False
    used_in_sections: list[str] = Field(default_factory=list)


class ProcessRound(BaseModel):
    round: int
    queries: list[str] = Field(default_factory=list)
    found: int = 0
    read: int = 0
    newly_added: int = 0
    gaps: list[str] = Field(default_factory=list)
    rationale: str = ""
    decision: str = ""


class ProcessSummary(BaseModel):
    """「研究过程记录」章节的数据源。"""

    rounds: int = 0
    total_queries: int = 0
    total_sources_found: int = 0
    total_sources_read: int = 0
    total_notes: int = 0
    timeline: list[ProcessRound] = Field(default_factory=list)
    urls_read: list[dict[str, Any]] = Field(default_factory=list)


class Report(BaseModel):
    topic: str
    title: str = ""
    executive_summary: str = ""
    sections: list[ReportSection] = Field(default_factory=list)
    key_conclusions: list[Conclusion] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    confidence: ConfidenceReport = Field(default_factory=ConfidenceReport)
    process: ProcessSummary = Field(default_factory=ProcessSummary)
    as_of: datetime | None = None
    generated_at: datetime | None = None
    markdown: str = ""
    citation_stats: dict[str, Any] = Field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# 编排状态
# ══════════════════════════════════════════════════════════════


class ResearchState(BaseModel):
    """编排过程中唯一可变的状态对象。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    request: ResearchRequest
    status: RunStatus = RunStatus.init
    round: int = 0
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    queries: list[QueryRecord] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    reflections: list[ReflectionRecord] = Field(default_factory=list)
    stop_reason: str | None = None
    confidence: ConfidenceReport | None = None
    report: Report | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = None
    as_of: datetime | None = None

    # ── 便捷视图 ──────────────────────────────────────────────

    def source_by_sid(self, sid: str) -> Source | None:
        for s in self.sources:
            if s.sid == sid:
                return s
        return None

    @property
    def sid_map(self) -> dict[str, Source]:
        return {s.sid: s for s in self.sources}

    @property
    def readable_sources(self) -> list[Source]:
        return [s for s in self.sources if s.readable]

    @property
    def valid_sids(self) -> set[str]:
        """可以作为引用依据的编号集合。"""
        return {s.sid for s in self.readable_sources}

    def notes_for_qid(self, qid: str) -> list[Note]:
        return [n for n in self.notes if n.qid == qid]

    def next_sid(self) -> str:
        return f"S{len(self.sources) + 1}"

    @property
    def info_cutoff(self) -> datetime | None:
        dates = [s.published_at for s in self.sources if s.published_at]
        return max(dates) if dates else None


__all__ = [
    "Conclusion",
    "ConfidenceFactors",
    "ConfidenceReport",
    "ContentOrigin",
    "FetchStatus",
    "Note",
    "ProcessRound",
    "ProcessSummary",
    "QueryRecord",
    "RawSearchResult",
    "ReflectionRecord",
    "Report",
    "ReportSection",
    "ResearchRequest",
    "ResearchState",
    "RunStatus",
    "SearchOutcome",
    "Source",
    "SourceRef",
    "StopReason",
    "SubQuestion",
]
