"""本目录内所有 pydantic 模型。

集中定义四类产物（report / report_html / sources / process / confidence）及其
中间结构（agent 输出 schema 等）。`agent/` 与 `templates/` 不定义任何模型。
"""
from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


# ---------------- agent 中间输出结构 ----------------

class KeywordOutput(BaseModel):
    """KeywordAgent.generate_keywords 的输出：一组搜索关键词。"""
    keywords: list[str] = Field(default_factory=list)


class JudgeDecision(BaseModel):
    """JudgeAgent.judge 的输出：是否补检 + 新关键词。"""
    sufficient: bool = False
    reason: str = ""
    new_keywords: list[str] = Field(default_factory=list)


class DraftBlock(BaseModel):
    """报告的一个分节：heading 来自关键词，body 来自该关键词的总结正文。"""
    heading: str
    body: str


class ReportOutline(BaseModel):
    """ReportAgent 第一次调用：报告元信息（正文分节由草稿段落直接映射）。"""
    title: str
    summary: str
    key_conclusions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ReportContent(ReportOutline):
    """结构化报告产物 = 报告元信息 + 分节正文。"""
    sections: list[DraftBlock] = Field(default_factory=list)


# ---------------- 研究过程 ----------------

class ProcessStep(BaseModel):
    """研究循环里的一步：plan / search / summarize / judge。"""
    kind: str  # plan | search | summarize | judge
    detail: str = ""


class ResearchProcess(BaseModel):
    """研究过程记录：检了哪些关键词、读了哪些页面、迭代了几轮。"""
    topic: str = ""
    plan: list[str] = Field(default_factory=list)          # 初始关键词
    search_queries: list[str] = Field(default_factory=list)  # 全部检索关键词（去重）
    reviewed_urls: list[str] = Field(default_factory=list)    # 来源 URL（去重）
    iterations: int = 0                                      # 检索轮数
    steps: list[ProcessStep] = Field(default_factory=list)   # 每步记录


# ---------------- 来源 ----------------

class Source(BaseModel):
    """一条可追溯来源（结论关联 URL / 标题 / 站点）。"""
    url: str
    title: str = ""
    snippet: str = ""
    site_name: str = ""
    date: str = ""


# ---------------- 置信度 ----------------

class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ConfidenceInfo(BaseModel):
    """置信度说明：结论可靠程度、信息截止时间；无来源结论标注为模型推断。"""
    level: ConfidenceLevel = ConfidenceLevel.LOW
    information_cutoff: str = ""   # 信息截止时间（日期）
    source_count: int = 0
    iteration_count: int = 0
    note: str = "无来源结论由模型推断生成。"


# ---------------- 研究记录（落盘/返回） ----------------

class ResearchRecord(BaseModel):
    """接口层研究记录：单次研究的完整产物 + 状态机。"""
    id: str
    status: str = "pending"  # pending | running | completed | failed
    topic: str = ""
    report: ReportContent | None = None
    report_html: str | None = None
    sources: list[Source] = Field(default_factory=list)
    process: ResearchProcess = Field(default_factory=ResearchProcess)
    confidence: ConfidenceInfo | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""


class ResearchResult(BaseModel):
    """研究引擎返回值：完整四类产物（供 research.py 包装成 ResearchRecord）。"""
    report: ReportContent
    report_html: str
    sources: list[Source] = Field(default_factory=list)
    process: ResearchProcess = Field(default_factory=ResearchProcess)
    confidence: ConfidenceInfo


if __name__ == "__main__":
    print("=== 模型序列化自检 ===")
    src_a = Source(url="https://a.example/", title="来源 A", site_name="例站", date="2026-09-01")
    proc = ResearchProcess(
        topic="天空为什么是蓝色的",
        plan=["蓝色成因", "波长散射"],
        search_queries=["蓝色成因", "波长散射"],
        reviewed_urls=[src_a.url],
        iterations=1,
        steps=[ProcessStep(kind="plan", detail="生成 2 个关键词")],
    )
    conf = ConfidenceInfo(level=ConfidenceLevel.MEDIUM, information_cutoff="2026-09-09", source_count=1, iteration_count=1)
    report = ReportContent(
        title="天空为什么是蓝色的",
        summary="摘要示例",
        key_conclusions=["瑞利散射"],
        open_questions=["夜间为何偏蓝"],
        sections=[DraftBlock(heading="蓝色成因", body="正文示例")],
    )
    rec = ResearchRecord(
        id="demo-id", status="completed", topic=proc.topic,
        report=report, report_html="<html>...</html>", sources=[src_a],
        process=proc, confidence=conf,
        created_at="2026-09-09T00:00:00", updated_at="2026-09-09T00:01:00",
    )
    data = rec.model_dump()
    rec2 = ResearchRecord.model_validate(data)
    assert rec2 == rec, "round-trip 不一致"
    print("ResearchRecord round-trip OK")
    print("report 分节数 :", len(rec2.report.sections))
    print("置信度       :", rec2.confidence.level.value)
    print("邮箱/关键词  :", rec2.process.search_queries)