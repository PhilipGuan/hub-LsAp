"""核心数据模型（pydantic v2），集中定义四类产物的结构。

四类产物对应关系：
- 结构化报告  → ResearchRecord.summary / sections / key_conclusions / open_questions
- 来源列表    → ResearchRecord.sources
- 研究过程    → ResearchRecord.process
- 置信度说明  → ResearchRecord.confidence
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def now_iso() -> str:
    """本地时间 ISO 字符串，用于落盘时间戳。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def make_slug(topic: str) -> str:
    """生成研究标识 slug：<时间戳>-<主题截断>。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    t = re.sub(r"[^\w一-鿿]+", "-", topic).strip("-")[:20]
    return f"{ts}-{t or 'research'}"


class SubQuestion(BaseModel):
    """规划阶段拆出的一个子问题。"""
    question: str
    rationale: str = ""


class Source(BaseModel):
    """一条可追溯的来源。"""
    url: str
    title: str
    site: str = ""           # 站点名（如有）
    snippet: str = ""        # 短摘要
    content: str = ""        # 长摘要（summary=true 返回，供阅读抽取使用）
    published_at: str = ""   # 发布时间（如有）


class Evidence(BaseModel):
    """一条带来源的证据/结论。source_index 为 None 表示「模型推断」（无来源）。"""
    claim: str
    source_index: int | None = None   # 指向 sources 列表下标；None = 无来源
    quote: str = ""                   # 支撑结论的原文引用


class SearchRound(BaseModel):
    """一轮检索的记录。"""
    round_no: int
    purpose: str = ""                          # 首轮检索 / 补检
    queries: list[str] = Field(default_factory=list)
    pages_read: list[Source] = Field(default_factory=list)
    extracted: list[Evidence] = Field(default_factory=list)
    decision: str = ""                         # LLM 补检判定（是否需补检及理由）


class ResearchProcess(BaseModel):
    """研究过程记录（第三类产物）。"""
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    rounds: list[SearchRound] = Field(default_factory=list)
    iterations: int = 0


class Section(BaseModel):
    """报告正文的一个分节。"""
    heading: str
    content: str                                  # Markdown 正文
    evidences: list[Evidence] = Field(default_factory=list)


class Confidence(BaseModel):
    """置信度说明（第四类产物）。"""
    level: Literal["high", "medium", "low"] = "medium"
    cutoff: str = ""                              # 信息截止时间
    notes: list[str] = Field(default_factory=list)


class ResearchRecord(BaseModel):
    """一次完整研究的产物（report.json 的唯一数据源，四类产物合一）。"""
    id: str                                        # slug
    topic: str
    created_at: str = Field(default_factory=now_iso)
    summary: str = ""                              # 摘要
    sections: list[Section] = Field(default_factory=list)
    key_conclusions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    process: ResearchProcess = Field(default_factory=ResearchProcess)
    confidence: Confidence = Field(default_factory=Confidence)
