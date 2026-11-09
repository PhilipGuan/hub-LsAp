# -*- coding: utf-8 -*-
"""数据模型：四类产物 + 中间结果（Pydantic v2）。

规格见 README.md / 任务说明书.md（T2）。
本模块只定义模型，不放业务逻辑；所有 pydantic 模型集中在此，
agent / templates 需要输出结构时从这里导入。
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# =====================================================================
# 基础枚举 / 请求
# =====================================================================


class Status(str, Enum):
    """研究任务状态机：pending → running → completed / failed。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ResearchRequest(BaseModel):
    """POST /api/research 的请求体。"""

    topic: str = Field(..., min_length=1, max_length=500, description="研究主题")


# =====================================================================
# 来源
# =====================================================================


class SourceRef(BaseModel):
    """结论引用到的来源（轻量引用，随结论一起出现）。"""

    url: str
    title: str


class Source(BaseModel):
    """来源列表中的一条（含摘要信息，便于追溯）。"""

    url: str
    title: str
    site_name: str = ""
    snippet: str = ""
    accessed_at: str = ""  # 访问日期 YYYY-MM-DD


# =====================================================================
# 各 agent 的中间输出结构
# =====================================================================


class KeywordOutput(BaseModel):
    """KeywordAgent 输出：拆解出的搜索关键词。"""

    keywords: list[str] = Field(default_factory=list)


class JudgeDecision(BaseModel):
    """JudgeAgent 输出：是否补检 + 新关键词。"""

    sufficient: bool = False
    reason: str = ""
    new_keywords: list[str] = Field(default_factory=list)


class DraftBlock(BaseModel):
    """报告草稿的一段正文（由某个关键词的搜索结果总结而来）。"""

    round: int
    keyword: str
    text: str


# =====================================================================
# 四类产物
# =====================================================================


class ProcessStep(BaseModel):
    """研究过程的一步：plan / search / summarize / judge。"""

    type: str  # plan | search | summarize | judge
    round: int
    detail: dict[str, Any] = Field(default_factory=dict)  # 结构化详情


class Conclusion(BaseModel):
    """关键结论：关联来源 URL / 标题，可追溯。"""

    text: str
    sources: list[SourceRef] = Field(default_factory=list)
    is_model_inference: bool = False  # 无来源支撑时必须为 True


class ReportOutline(BaseModel):
    """ReportAgent 输出的报告元信息（正文分节由草稿映射，不走 LLM）。"""

    title: str
    summary: str
    key_conclusions: list[Conclusion] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class Section(BaseModel):
    """报告的一个分节。"""

    heading: str
    body: str
    conclusions: list[Conclusion] = Field(default_factory=list)


class ConfidenceNote(BaseModel):
    """置信度说明。"""

    overall: str  # 高 / 中 / 低
    info_cutoff: str  # 信息截止时间
    notes: list[str] = Field(default_factory=list)  # 多条说明


class ReportContent(BaseModel):
    """结构化研究报告。"""

    title: str
    summary: str
    sections: list[Section] = Field(default_factory=list)
    key_conclusions: list[Conclusion] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ResearchProcess(BaseModel):
    """研究过程记录。"""

    plan: list[str] = Field(default_factory=list)  # 初始拆解的关键词
    search_queries: list[str] = Field(default_factory=list)  # 全部检索关键词
    reviewed_urls: list[str] = Field(default_factory=list)  # 已读页面 URL（去重）
    iterations: int = 0  # 迭代轮数
    steps: list[ProcessStep] = Field(default_factory=list)  # 每步明细


# =====================================================================
# 落盘记录 / 引擎返回值
# =====================================================================


class ResearchRecord(BaseModel):
    """落盘的一条完整研究记录（GET /api/research/{rid} 的响应体）。"""

    research_id: str
    topic: str
    status: Status = Status.PENDING
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    error: str | None = None
    report: ReportContent | None = None
    report_html: str | None = None
    sources: list[Source] = Field(default_factory=list)
    draft: list[DraftBlock] = Field(default_factory=list)
    process: ResearchProcess = Field(default_factory=ResearchProcess)
    confidence: ConfidenceNote | None = None


class DeepResearchResult(BaseModel):
    """DeepResearch 引擎一次完整研究的结果。"""

    report: ReportContent
    report_html: str
    sources: list[Source] = Field(default_factory=list)
    draft: list[DraftBlock] = Field(default_factory=list)
    process: ResearchProcess = Field(default_factory=ResearchProcess)
    confidence: ConfidenceNote | None = None


# =====================================================================
# 自检 demo（纯本地，不需要网络 / 密钥）
# =====================================================================
if __name__ == "__main__":
    src_ref = SourceRef(url="https://example.com/a", title="示例来源 A")
    source = Source(
        url="https://example.com/a",
        title="示例来源 A",
        site_name="example.com",
        snippet="这是一段示例摘要。",
        accessed_at="2026-09-10",
    )

    conclusion = Conclusion(
        text="带来源的可追溯结论。",
        sources=[src_ref],
    )
    inferred = Conclusion(
        text="没有来源支撑的推断。",
        sources=[],
        is_model_inference=True,
    )
    section = Section(
        heading="示例分节",
        body="分节正文。",
        conclusions=[conclusion, inferred],
    )

    record = ResearchRecord(
        research_id="demo-0001",
        topic="深度研究助手是什么",
        status=Status.COMPLETED,
        error=None,
        report=ReportContent(
            title="深度研究助手调研报告",
            summary="一句话摘要。",
            sections=[section],
            key_conclusions=[conclusion, inferred],
            open_questions=["遗留问题一？", "遗留问题二？"],
        ),
        report_html="<!doctype html><html><body><h1>报告</h1></body></html>",
        sources=[source],
        draft=[DraftBlock(round=1, keyword="深度研究", text="第一轮结论文字。")],
        process=ResearchProcess(
            plan=["深度研究"],
            search_queries=["深度研究", "自动调研工具"],
            reviewed_urls=["https://example.com/a"],
            iterations=1,
            steps=[
                ProcessStep(
                    type="plan",
                    round=0,
                    detail={"keywords": ["深度研究"], "count": 1},
                ),
                ProcessStep(
                    type="search",
                    round=1,
                    detail={"keyword": "深度研究", "results": 5},
                ),
                ProcessStep(
                    type="summarize",
                    round=1,
                    detail={"keyword": "深度研究", "chars": 120},
                ),
                ProcessStep(
                    type="judge",
                    round=1,
                    detail={"sufficient": True, "new_keywords": []},
                ),
            ],
        ),
        confidence=ConfidenceNote(
            overall="中",
            info_cutoff="2026-09-10",
            notes=["1 条结论有来源支撑。", "1 条为模型推断。"],
        ),
    )

    outline = ReportOutline(
        title="深度研究助手调研报告",
        summary="一句话摘要。",
        key_conclusions=[conclusion, inferred],
        open_questions=["遗留问题一？"],
    )

    # 1) 对象 → JSON 字符串
    payload = record.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    # 2) JSON 字符串 → 对象（round-trip 必须等价）
    restored = ResearchRecord.model_validate_json(text)
    assert restored == record, "序列化 → 反序列化后对象不相等"
    assert isinstance(restored, ResearchRecord)
    assert restored.status is Status.COMPLETED
    assert restored.created_at == record.created_at, "datetime 往返不一致"

    # 3) 模型 dict → 对象
    assert ResearchRecord.model_validate(payload) == record

    # 4) 请求校验：空 topic 应报错，超长 topic 应报错
    assert ResearchRequest(topic="合法主题").topic == "合法主题"
    for bad in ("", "x" * 501):
        try:
            ResearchRequest(topic=bad)
        except Exception:  # noqa: BLE001 - demo 只关心“确实被拒绝”
            pass
        else:
            raise AssertionError("非法 topic 未被拒绝")

    # 5) 引擎返回值模型
    result = DeepResearchResult(
        report=record.report,
        report_html=record.report_html or "",
        sources=record.sources,
        draft=record.draft,
        process=record.process,
        confidence=record.confidence,
    )
    restored_result = DeepResearchResult.model_validate_json(
        result.model_dump_json()
    )
    assert restored_result == result, "DeepResearchResult 往返不一致"

    # 6) 结构调整后的三个字段
    assert isinstance(record.process.steps[0].detail, dict), "ProcessStep.detail 应为 dict"
    assert restored.process.steps[1].detail == {"keyword": "深度研究", "results": 5}
    assert record.process.steps[0].detail == {"keywords": ["深度研究"], "count": 1}
    assert ProcessStep(type="plan", round=0).detail == {}, "detail 默认应为空 dict"
    assert isinstance(record.confidence.notes, list) and record.confidence.notes
    assert ConfidenceNote(overall="高", info_cutoff="2026-09-10").notes == []
    assert isinstance(outline.key_conclusions[0], Conclusion)
    assert outline.key_conclusions[1].is_model_inference is True
    assert ReportOutline.model_validate_json(outline.model_dump_json()) == outline

    print(f"序列化 JSON 长度 = {len(text)} 字符")
    print(f"模型清单 = {[m.__name__ for m in (Status, ResearchRequest, SourceRef, Source, KeywordOutput, JudgeDecision, DraftBlock, ProcessStep, Conclusion, Section, ConfidenceNote, ReportOutline, ReportContent, ResearchProcess, ResearchRecord, DeepResearchResult)]}")
    print("模型序列化自检 OK")
