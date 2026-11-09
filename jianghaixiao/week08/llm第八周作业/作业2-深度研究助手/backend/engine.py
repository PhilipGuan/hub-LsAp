"""研究引擎：编排关键词、搜索、总结、补检和报告。"""
from __future__ import annotations

import html
import inspect
from collections.abc import Awaitable, Callable

from . import config, llm, tools
from .models import (
    ConfidenceNote,
    KeyConclusion,
    ProcessStep,
    ReportContent,
    ReportSection,
    ResearchProcess,
    ResearchResult,
    Source,
)


class DeepResearch:
    def __init__(self, topic: str):
        self.topic = topic

    async def run(
        self,
        on_progress: Callable[[dict], Awaitable[None] | None] | None = None,
    ) -> ResearchResult:
        process = ResearchProcess()
        sources: list[Source] = []
        seen_urls: set[str] = set()
        draft: list[ReportSection] = []

        keywords = await llm.generate_keywords(self.topic)
        process.plan = keywords
        process.steps.append(
            ProcessStep(type="plan", detail={"keywords": keywords})
        )
        await self._progress(on_progress, process, draft, sources)

        searched: list[str] = []
        todo = list(keywords)

        for round_no in range(1, config.MAX_ROUNDS + 1):
            process.iterations = round_no
            for keyword in todo:
                if keyword in searched:
                    continue
                searched.append(keyword)
                process.search_queries.append(keyword)

                try:
                    results = await tools.web_search(keyword)
                except Exception:
                    results = []

                self._collect_sources(results, seen_urls, sources, process)
                process.steps.append(
                    ProcessStep(
                        type="search",
                        round=round_no,
                        detail={"keyword": keyword, "results": len(results)},
                    )
                )

                text = await llm.summarize(self.topic, keyword, results)
                draft.append(ReportSection(heading=keyword, body=text))
                process.steps.append(
                    ProcessStep(
                        type="summarize",
                        round=round_no,
                        detail={"keyword": keyword, "chars": len(text)},
                    )
                )

            draft_text = self._join_draft(draft)
            decision = await llm.judge(
                self.topic,
                draft_text,
                len(sources),
                searched,
                round_no,
            )
            process.steps.append(
                ProcessStep(
                    type="judge",
                    round=round_no,
                    detail=decision,
                )
            )
            await self._progress(on_progress, process, draft, sources)

            if decision.get("sufficient") or round_no >= config.MAX_ROUNDS:
                break
            todo = [
                str(item)
                for item in decision.get("new_keywords", [])
                if str(item) and str(item) not in searched
            ]
            if not todo:
                break

        confidence = self._compute_confidence(sources, draft)
        metadata = await llm.report_metadata(
            self.topic,
            self._join_draft(draft),
            [source.model_dump() for source in sources],
        )
        conclusions: list[KeyConclusion] = []
        for item in metadata.get("key_conclusions", []):
            conclusions.append(
                KeyConclusion(
                    text=str(item.get("text", "")).strip(),
                    source_urls=[
                        str(url)
                        for url in item.get("source_urls", [])
                        if str(url)
                    ],
                    is_model_inference=bool(item.get("is_model_inference", False)),
                )
            )

        report = ReportContent(
            title=str(metadata.get("title") or f"{self.topic}研究报告"),
            summary=str(metadata.get("summary") or self._join_draft(draft)[:500]),
            sections=draft,
            key_conclusions=conclusions,
            open_questions=[
                str(item) for item in metadata.get("open_questions", []) if str(item)
            ],
        )
        report_html = render_report_html(report, sources, confidence)
        return ResearchResult(
            report=report,
            report_html=report_html,
            sources=sources,
            draft=draft,
            process=process,
            confidence=confidence,
        )

    @staticmethod
    def _collect_sources(
        results: list[dict],
        seen_urls: set[str],
        sources: list[Source],
        process: ResearchProcess,
    ) -> None:
        for item in results:
            url = str(item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            process.reviewed_urls.append(url)
            sources.append(
                Source(
                    url=url,
                    title=str(item.get("title") or ""),
                    site_name=str(item.get("site_name") or ""),
                    snippet=str(item.get("snippet") or ""),
                    date=str(item.get("date") or ""),
                )
            )

    @staticmethod
    def _join_draft(draft: list[ReportSection]) -> str:
        return "\n\n".join(
            f"【{section.heading}】\n{section.body}" for section in draft
        )

    @staticmethod
    def _compute_confidence(
        sources: list[Source],
        draft: list[ReportSection],
    ) -> ConfidenceNote:
        if len(sources) >= 8:
            overall = "high"
        elif len(sources) >= 4:
            overall = "medium"
        else:
            overall = "low"
        return ConfidenceNote(
            overall=overall,
            info_cutoff=config.today_str(),
            notes=[
                f"共整理 {len(draft)} 个正文段落，引用 {len(sources)} 个来源。",
                "mock 模式的来源仅用于演示流程，不代表真实调研结论。",
            ],
        )

    @staticmethod
    async def _progress(
        callback: Callable[[dict], Awaitable[None] | None] | None,
        process: ResearchProcess,
        draft: list[ReportSection],
        sources: list[Source],
    ) -> None:
        if callback is None:
            return
        result = callback(
            {
                "process": process.model_dump(),
                "draft": [item.model_dump() for item in draft],
                "sources": [item.model_dump() for item in sources],
            }
        )
        if inspect.isawaitable(result):
            await result


def render_report_html(
    report: ReportContent,
    sources: list[Source],
    confidence: ConfidenceNote,
) -> str:
    sections = "\n".join(
        f"<section><h2>{html.escape(section.heading)}</h2>"
        f"<p>{html.escape(section.body).replace(chr(10), '<br>')}</p></section>"
        for section in report.sections
    )
    conclusions = "\n".join(
        f"<li>{html.escape(item.text)}"
        + ("（模型推断）" if item.is_model_inference else "")
        + "</li>"
        for item in report.key_conclusions
    )
    source_items = "\n".join(
        f'<li><a href="{html.escape(source.url)}">{html.escape(source.title or source.url)}</a></li>'
        for source in sources
    )
    questions = "\n".join(
        f"<li>{html.escape(item)}</li>" for item in report.open_questions
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{html.escape(report.title)}</title>
  <style>
    body {{ max-width: 900px; margin: 40px auto; padding: 0 24px;
      color: #1f2937; font-family: "Microsoft YaHei", sans-serif; line-height: 1.8; }}
    h1 {{ color: #0f766e; }}
    section {{ margin: 28px 0; padding-bottom: 16px; border-bottom: 1px solid #e5e7eb; }}
    li {{ margin: 8px 0; }}
    .meta {{ color: #64748b; }}
  </style>
</head>
<body>
  <h1>{html.escape(report.title)}</h1>
  <p class="meta">信息截止：{html.escape(confidence.info_cutoff)}；
    置信度：{html.escape(confidence.overall)}</p>
  <section><h2>摘要</h2><p>{html.escape(report.summary)}</p></section>
  {sections}
  <section><h2>关键结论</h2><ul>{conclusions}</ul></section>
  <section><h2>遗留问题</h2><ul>{questions}</ul></section>
  <section><h2>来源</h2><ol>{source_items}</ol></section>
</body>
</html>"""
