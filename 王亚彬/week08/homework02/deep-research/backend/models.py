"""数据模型：搜索结果、研究过程、最终报告。"""
from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


class SearchResult(BaseModel):
    """Bocha 返回的一条网页结果。"""

    title: str = ""
    url: str = ""
    snippet: str = ""
    summary: str = ""
    site_name: str = ""
    publish_time: str = ""

    @property
    def text(self) -> str:
        return self.summary or self.snippet


class Source(BaseModel):
    """报告引用的来源，带编号便于正文回溯。"""

    id: str = Field(default_factory=new_id)
    title: str = ""
    url: str = ""
    site_name: str = ""
    snippet: str = ""
    used_in: list[str] = Field(default_factory=list)


class PageReading(BaseModel):
    """对一个页面的阅读抽取结果。"""

    url: str = ""
    title: str = ""
    facts: list[str] = Field(default_factory=list)
    ok: bool = True
    error: str = ""


class SubQuestion(BaseModel):
    """规划阶段拆出的子问题。"""

    id: str = Field(default_factory=new_id)
    question: str = ""
    keywords: list[str] = Field(default_factory=list)
    round_added: int = 1
    status: str = "pending"  # pending / done / insufficient
    facts: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class ProcessStep(BaseModel):
    """研究过程记录，用于可追溯。"""

    round: int = 0
    action: str = ""  # plan / search / read / judge / write
    detail: str = ""
    ts: float = Field(default_factory=now)


class ReportSection(BaseModel):
    heading: str = ""
    content: str = ""
    source_ids: list[str] = Field(default_factory=list)


class Confidence(BaseModel):
    """置信度说明。"""

    level: str = "中"  # 高 / 中 / 低
    reason: str = ""
    source_count: int = 0
    rounds_used: int = 0
    cutoff_note: str = ""


class ResearchReport(BaseModel):
    """最终研究报告。"""

    topic: str = ""
    summary: str = ""
    sections: list[ReportSection] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    sources: list[Source] = Field(default_factory=list)
    process: list[ProcessStep] = Field(default_factory=list)
    rounds_used: int = 0
    created_at: float = Field(default_factory=now)
    elapsed_sec: float = 0.0

    def to_markdown(self) -> str:
        lines: list[str] = [f"# {self.topic}", "", "## 摘要", "", self.summary, ""]
        for sec in self.sections:
            lines.append(f"## {sec.heading}")
            lines.append("")
            lines.append(sec.content)
            lines.append("")
        if self.key_findings:
            lines.append("## 关键结论")
            lines.append("")
            for i, k in enumerate(self.key_findings, 1):
                lines.append(f"{i}. {k}")
            lines.append("")
        if self.open_questions:
            lines.append("## 遗留问题")
            lines.append("")
            for i, q in enumerate(self.open_questions, 1):
                lines.append(f"{i}. {q}")
            lines.append("")
        c = self.confidence
        lines.append("## 置信度说明")
        lines.append("")
        lines.append(f"- 等级：{c.level}")
        lines.append(f"- 依据：{c.reason}")
        lines.append(f"- 引用来源数：{c.source_count}")
        lines.append(f"- 检索轮次：{c.rounds_used}")
        if c.cutoff_note:
            lines.append(f"- 信息截止：{c.cutoff_note}")
        lines.append("")
        lines.append("## 来源列表")
        lines.append("")
        for i, s in enumerate(self.sources, 1):
            title = s.title or s.url
            lines.append(f"{i}. [{title}]({s.url})  ")
            lines.append(f"   来源：{s.site_name or '未知'}")
            lines.append("")
        lines.append("## 研究过程")
        lines.append("")
        for p in self.process:
            lines.append(f"- 第{p.round}轮 · {p.action}：{p.detail}")
        return "\n".join(lines) + "\n"


class ResearchTask(BaseModel):
    """一次研究任务的完整状态，落盘为 JSON。"""

    id: str = Field(default_factory=new_id)
    topic: str = ""
    status: str = "pending"  # pending / running / completed / failed
    stage: str = ""  # plan / search / read / judge / write
    progress: int = 0
    error: str = ""
    keywords_used: list[str] = Field(default_factory=list)
    pages_read: list[str] = Field(default_factory=list)
    rounds_used: int = 0
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)
    report: ResearchReport | None = None

    def touch(self) -> None:
        self.updated_at = now()

    def summary_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "rounds_used": self.rounds_used,
            "keywords_used": self.keywords_used,
            "pages_read": self.pages_read,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


if __name__ == "__main__":
    r = SearchResult(title="测试", url="https://example.com", summary="摘要内容", site_name="example")
    print("SearchResult:", r.model_dump())

    rep = ResearchReport(
        topic="测试主题",
        summary="这是摘要。",
        sections=[ReportSection(heading="第一节", content="正文内容。", source_ids=["s1"])],
        key_findings=["结论一"],
        open_questions=["待查问题"],
        confidence=Confidence(level="中", reason="来源较少", source_count=3, rounds_used=2),
        sources=[Source(id="s1", title="测试来源", url="https://example.com", site_name="example")],
        process=[ProcessStep(round=1, action="search", detail="关键词：测试")],
    )
    print()
    print("--- Markdown 渲染 ---")
    print(rep.to_markdown())

    t = ResearchTask(topic="测试主题", status="running", stage="search", progress=40)
    print("task:", t.summary_dict())
