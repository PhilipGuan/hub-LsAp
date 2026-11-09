"""API、报告、过程和落盘记录的数据模型。"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Status(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500, description="研究主题")


class ResearchCreated(BaseModel):
    research_id: str
    status: Status = Status.pending


class Source(BaseModel):
    url: str
    title: str = ""
    site_name: str = ""
    snippet: str = ""
    date: str = ""


class ProcessStep(BaseModel):
    type: str
    round: int = 0
    detail: dict = Field(default_factory=dict)


class ResearchProcess(BaseModel):
    plan: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    reviewed_urls: list[str] = Field(default_factory=list)
    iterations: int = 0
    steps: list[ProcessStep] = Field(default_factory=list)


class ReportSection(BaseModel):
    heading: str
    body: str


class KeyConclusion(BaseModel):
    text: str
    source_urls: list[str] = Field(default_factory=list)
    is_model_inference: bool = False


class ReportContent(BaseModel):
    title: str
    summary: str
    sections: list[ReportSection] = Field(default_factory=list)
    key_conclusions: list[KeyConclusion] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ConfidenceNote(BaseModel):
    overall: str = "low"
    info_cutoff: str = ""
    notes: list[str] = Field(default_factory=list)


class ResearchRecord(BaseModel):
    research_id: str
    topic: str
    status: Status
    created_at: str
    updated_at: str
    error: str | None = None
    report: ReportContent | None = None
    report_html: str = ""
    sources: list[Source] = Field(default_factory=list)
    draft: list[ReportSection] = Field(default_factory=list)
    process: ResearchProcess = Field(default_factory=ResearchProcess)
    confidence: ConfidenceNote | None = None


class ResearchResult(BaseModel):
    report: ReportContent
    report_html: str
    sources: list[Source]
    draft: list[ReportSection]
    process: ResearchProcess
    confidence: ConfidenceNote
