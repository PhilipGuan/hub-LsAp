"""Dataclasses for the research pipeline."""
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str = ""
    summary: str = ""
    source: str = ""  # site name if provided by the API

    def cite(self, n: int) -> str:
        return f"[{n}] {self.title} — {self.url}"


@dataclass
class SubQuestion:
    question: str
    rationale: str = ""


@dataclass
class Finding:
    sub_question: str
    answer: str
    sources: list[SearchResult] = field(default_factory=list)
    confidence: float = 0.5  # 0..1, set by the extractor
    info_cutoff: str = ""    # YYYY-MM-DD if known


@dataclass
class ResearchReport:
    topic: str
    summary: str
    sections: list[dict]          # [{"title":..., "body":..., "citations":[1,2]}]
    sources: list[SearchResult]
    process_log: list[str]
    open_questions: list[str]
    key_conclusions: list[str]
    generated_at: str

    def to_markdown(self) -> str:
        L: list[str] = [
            f"# 深度研究：{self.topic}",
            "",
            f"_生成时间：{self.generated_at}_",
            "",
            "## 摘要",
            "",
            self.summary.strip(),
            "",
        ]
        for sec in self.sections:
            L += [f"## {sec['title']}", "", sec["body"].strip(), ""]

        if self.key_conclusions:
            L += ["## 关键结论", ""]
            L += [f"- {c}" for c in self.key_conclusions]
            L.append("")

        if self.open_questions:
            L += ["## 遗留问题 / Open Questions", ""]
            L += [f"- {q}" for q in self.open_questions]
            L.append("")

        L += ["## 来源", ""]
        for i, s in enumerate(self.sources, 1):
            L.append(s.cite(i))
        L.append("")

        if self.process_log:
            L += ["## 研究过程", ""]
            L += [f"- {p}" for p in self.process_log]

        # Ponytail: no template engine, no Jinja — f-string + list-join is enough.
        return "\n".join(L)