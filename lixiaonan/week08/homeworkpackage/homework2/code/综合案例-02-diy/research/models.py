"""数据结构与来源登记表（S2）。

核心约定（对应 TECH_DESIGN 第 3 节）：
- 每个唯一 URL 在 SourceRegistry 中分配一个 sid（S1、S2…）；
- Finding / Conclusion 必须携带 source_ids，为空即视为"模型推断"；
- TraceLogger 逐事件追加 trace.jsonl，可回放审计。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator

Confidence = Literal["high", "medium", "low"]


def _as_sid_list(v: Any) -> Any:
    """容错：LLM 偶发把来源编号输出成 "S1, S2" 这类字符串，自动拆成列表。"""
    if isinstance(v, str):
        return [s.strip() for s in v.replace("，", ",").split(",") if s.strip()]
    return v

# ---------- 阶段间流转的数据模型 ----------


class SearchHit(BaseModel):
    """博查搜索返回的单条结果。"""

    url: str
    title: str
    summary: str = ""
    date_published: str = ""


class Source(BaseModel):
    """来源登记表中的一条来源（全流程唯一 sid）。"""

    sid: str
    url: str
    title: str
    snippet: str = ""
    date_published: str = ""


class Finding(BaseModel):
    """从来源中抽取的一条材料；source_ids 为空即"模型推断"。"""

    content: str
    source_ids: list[str] = Field(default_factory=list)
    related_sub_id: str = ""
    confidence: Confidence = "low"

    @field_validator("source_ids", mode="before")
    @classmethod
    def _split_source_ids(cls, v: Any) -> Any:
        return _as_sid_list(v)

    @property
    def is_inference(self) -> bool:
        return not self.source_ids


class SubQuestion(BaseModel):
    """规划阶段拆出的子问题及其研究状态。"""

    id: str
    question: str
    status: Literal["pending", "resolved", "abandoned"] = "pending"
    keywords_tried: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    """① 规划阶段的输出。"""

    subquestions: list[SubQuestion]


class Keywords(BaseModel):
    """② 关键词生成阶段的输出。"""

    queries: list[str]


class Extraction(BaseModel):
    """③ 阅读抽取阶段的整体输出。"""

    findings: list[Finding] = Field(default_factory=list)


class Judgement(BaseModel):
    """④ 反思阶段的判定。"""

    sub_id: str = ""
    status: Literal["resolved", "need_more", "abandoned"]
    reason: str = ""
    extra_keywords: list[str] = Field(default_factory=list)


class Section(BaseModel):
    """报告章节。"""

    heading: str
    body: str
    refs: list[str] = Field(default_factory=list)

    @field_validator("refs", mode="before")
    @classmethod
    def _split_refs(cls, v: Any) -> Any:
        return _as_sid_list(v)


class Conclusion(BaseModel):
    """报告关键结论；refs 为空即"模型推断"。"""

    text: str
    refs: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"

    @field_validator("refs", mode="before")
    @classmethod
    def _split_refs(cls, v: Any) -> Any:
        return _as_sid_list(v)

    @property
    def is_inference(self) -> bool:
        return not self.refs


class Report(BaseModel):
    """⑤ 综合阶段的最终报告。"""

    title: str
    summary: str
    sections: list[Section] = Field(default_factory=list)
    key_conclusions: list[Conclusion] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence_notes: list[str] = Field(default_factory=list)
    info_cutoff: str = "未知，建议核实时效"


class TraceEvent(BaseModel):
    """过程记录事件（逐条写入 trace.jsonl）。"""

    round_no: int = 0
    action: Literal["plan", "search", "read", "reflect", "synthesize", "warn"]
    detail: dict[str, Any] = Field(default_factory=dict)


# ---------- 来源登记表 ----------

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "from", "share_token",
}


def normalize_url(url: str) -> str:
    """URL 规范化：小写 scheme/host、去 fragment、去常见跟踪参数，用于去重。"""
    url = url.strip()
    try:
        parts = urlsplit(url)
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS
        ]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))
    except ValueError:
        return url


class SourceRegistry:
    """来源登记表：保证"每条结论可追溯"的核心数据结构。"""

    def __init__(self) -> None:
        self._by_sid: dict[str, Source] = {}
        self._by_url: dict[str, str] = {}  # 规范化 URL -> sid
        self.findings: list[Finding] = []

    def register(self, hit: SearchHit) -> tuple[Source, bool]:
        """登记搜索结果；URL 已存在时返回已有来源（is_new=False），不重复分配 sid。"""
        url = hit.url.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"非法 URL：{url!r}")
        key = normalize_url(url)
        if key in self._by_url:
            return self._by_sid[self._by_url[key]], False
        sid = f"S{len(self._by_sid) + 1}"
        source = Source(sid=sid, url=url, title=hit.title or url, snippet=hit.summary, date_published=hit.date_published)
        self._by_sid[sid] = source
        self._by_url[key] = sid
        return source, True

    def seen(self, url: str) -> bool:
        return normalize_url(url) in self._by_url

    def get(self, sid: str) -> Source | None:
        return self._by_sid.get(sid)

    def all_sources(self) -> list[Source]:
        return list(self._by_sid.values())

    def valid_sids(self) -> set[str]:
        return set(self._by_sid)

    def add_finding(self, finding: Finding) -> Finding | None:
        """收编材料：剔除登记表中不存在的 sid（防幻觉引用）；引用全部非法则丢弃。"""
        valid = [s for s in finding.source_ids if s in self._by_sid]
        if finding.source_ids and not valid:
            return None
        cleaned = finding.model_copy(update={"source_ids": valid, "content": finding.content.strip()[:500]})
        self.findings.append(cleaned)
        return cleaned

    def findings_by_sub(self, sub_id: str) -> list[Finding]:
        return [f for f in self.findings if f.related_sub_id == sub_id]

    def latest_date(self) -> str:
        """来源中可识别的最新时间（尽力而为），用于"信息截止时间"。"""
        latest = ""
        for source in self._by_sid.values():
            raw = (source.date_published or "").strip()
            if not raw:
                continue
            day = raw[:10]
            try:
                datetime.strptime(day, "%Y-%m-%d")
            except ValueError:
                continue
            if day > latest:
                latest = day
        return latest


class TraceLogger:
    """研究过程记录：逐事件追加 trace.jsonl（可回放审计）。"""

    def __init__(self, path: Path | None = None) -> None:
        self.events: list[TraceEvent] = []
        self._path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def log(self, action: str, round_no: int = 0, **detail: Any) -> None:
        event = TraceEvent(round_no=round_no, action=action, detail=detail)
        self.events.append(event)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
