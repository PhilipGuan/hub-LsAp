# -*- coding: utf-8 -*-
"""Pydantic 模型汇总（Dry-Run 空壳占位 · 字段完整对齐 Spec §6）。

所有模块间通信只依赖这里的 Pydantic 类型，禁止传内部状态对象。
正式实现时逐个字段/验证器补全，签名保持不变。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ============================================================
# M1 · DataProvider 返回值类型（结构化数据 + B2 source_tag + as_of）
# ============================================================
class ETFProfile(BaseModel):
    model_config = {"extra": "allow"}

    ticker: str
    name: str = ""
    issuer: str = ""
    isin: str = ""
    inception_date: date | None = None
    category: str = ""
    focus_index: str = ""
    expense_ratio: float | None = None
    aum_usd_m: float | None = None
    market_cap_usd_m: float | None = None
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    dividend_yield_pct: float | None = None
    ytd_return_pct: float | None = None
    one_y_return_pct: float | None = None
    three_y_annual_pct: float | None = None
    five_y_annual_pct: float | None = None
    as_of: date | None = None
    source_tag: str = ""

class QuoteRow(BaseModel):
    date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    adj_close: float | None = None
    volume: int | None = None

class QuoteHistory(BaseModel):
    ticker: str
    period: str = ""
    rows: list[QuoteRow] = []
    as_of: date | None = None
    source_tag: str = ""

class RiskMetrics(BaseModel):
    ticker: str
    volatility_1y_pct: float | None = None
    max_drawdown_pct: float | None = None
    sharpe_3y: float | None = None
    sortino_3y: float | None = None
    beta: float | None = None
    alpha_pct: float | None = None
    tracking_error_pct: float | None = None
    correlation_to_benchmark: float | None = None
    as_of: date | None = None
    source_tag: str = ""

class HoldingItem(BaseModel):
    name: str = ""
    ticker: str = ""
    weight_pct: float | None = None
    sector: str = ""

class Holdings(BaseModel):
    ticker: str
    top_holdings: list[HoldingItem] = []
    sector_weights: dict[str, float] = Field(default_factory=dict)
    as_of: date | None = None
    source_tag: str = ""

class FundFlow(BaseModel):
    ticker: str
    flow_5d_usd_m: float | None = None
    flow_20d_usd_m: float | None = None
    flow_60d_usd_m: float | None = None
    flow_ytd_usd_m: float | None = None
    as_of: date | None = None
    source_tag: str = ""

class PeerProfileMini(BaseModel):
    ticker: str
    name: str = ""
    category: str = ""
    focus_index: str = ""
    aum_usd_m: float | None = None

class PeerResolution(BaseModel):
    peer_tickers: list[str] = []
    peer_basis: str = ""
    peer_profiles: list[PeerProfileMini] = []

class PeerInfoRow(BaseModel):
    ticker: str
    name: str = ""
    aum_usd_m: float | None = None
    expense_ratio: float | None = None
    ytd_return_pct: float | None = None
    one_y_return_pct: float | None = None
    three_y_annual_pct: float | None = None
    sharpe_3y: float | None = None
    max_drawdown_pct: float | None = None
    beta: float | None = None
    dividend_yield_pct: float | None = None
    as_of: date | None = None
    source_tag: str = ""

class PeerInfoBatch(BaseModel):
    """DataProvider.get_peer_info 返回值：批量 peer 单行指标。"""
    rows: list[PeerInfoRow] = []
    as_of: date | None = None
    source_tag: str = ""


class PeerComparisonMatrix(BaseModel):
    main_ticker: str = ""
    rows: list[PeerInfoRow] = []
    as_of: date | None = None

class StaleField(BaseModel):
    field_path: str
    as_of: date | None = None
    age_days: int = 0
    policy_max_days: int = 0
    suggested_action: Literal["bypass_cache_refetch", "degrade_mark_warning"] = "degrade_mark_warning"

# ============================================================
# M3 · Citation / TimeWindow 类型
# ============================================================
class Citation(BaseModel):
    tag: str
    provider: str
    ref_index: int
    source_url: str | None = None
    snippet: str | None = None
    as_of: date | None = None

class CitedParagraph(BaseModel):
    section_id: str
    plain_text: str = ""
    citations: list[Citation] = []

# ============================================================
# M4 · Disclaimer / Exporter / Report 类型
# ============================================================
class Disclaimer(BaseModel):
    version: str = "v1.0"
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    statements: list[str] = Field(default_factory=lambda: [
        "本报告由 AI 自动生成，不构成任何投资建议或邀约。",
        "历史业绩不代表未来表现，投资有风险，入市需谨慎。",
        "数据来源于公开第三方渠道，不保证其及时性与准确性。",
        "所有决策请咨询持牌金融顾问，本工具不对任何损失承担责任。",
    ])

class AnalystRating(BaseModel):
    rating: Literal["bullish", "neutral", "bearish"] = "neutral"
    key_drivers: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    note: str = "仅为 LLM 生成的主题分类标签，非投资建议。"

class Confidence(BaseModel):
    overall: Literal["high", "medium", "low"] = "low"
    info_cutoff: date | None = None
    notes: list[str] = Field(default_factory=list)

class StepLog(BaseModel):
    type: str = ""
    message: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    extra: dict[str, Any] = Field(default_factory=dict)

class ProcessLog(BaseModel):
    plan: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    reviewed_urls: list[str] = Field(default_factory=list)
    iterations: int = 0
    steps: list[StepLog] = Field(default_factory=list)

class ReportContent(BaseModel):
    ticker: str
    report_date: date
    style: Literal["analyst", "institutional", "explainer"] = "analyst"
    sections: list[CitedParagraph] = Field(default_factory=list)
    peer_matrix: PeerComparisonMatrix | None = None
    analyst_rating: AnalystRating | None = None
    stale_warnings: list[str] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    process: ProcessLog = Field(default_factory=ProcessLog)
    disclaimer: Disclaimer = Field(default_factory=Disclaimer)

# ============================================================
# M6 · Storage / API 响应模型（保持与综合案例-02 同形）
# ============================================================
class ResearchRecord(BaseModel):
    research_id: str
    ticker: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    style: Literal["analyst", "institutional", "explainer"] = "analyst"
    peers_requested: int = 5
    report: ReportContent | None = None
    error: str | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    draft: list[dict[str, Any]] = Field(default_factory=list)
    process: ProcessLog = Field(default_factory=ProcessLog)
