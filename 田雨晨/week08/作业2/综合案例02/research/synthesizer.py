"""All findings → final structured ResearchReport."""
from datetime import datetime, timezone

from .llm import LLM
from .models import Finding, ResearchReport, SearchResult

SYNTH_SYSTEM = """You are a research report writer.
Synthesize the findings into a structured report.
- Pick section titles that match the natural shape of the topic (usually 3-6 sections).
- Cite sources inline with [n] referencing the numbered source index below.
- End with a short "key_conclusions" bullet list and an "open_questions" bullet list.
Return JSON:
{
  "summary": "executive summary, 3-5 sentences",
  "sections": [{"title": "...", "body": "...", "citations": [1,2]}],
  "key_conclusions": ["..."],
  "open_questions": ["..."]
}
No prose, no markdown fence."""


def synthesize(topic: str, findings: list[Finding], llm: LLM) -> ResearchReport:
    # Dedup sources across all findings, preserving first-seen order.
    seen: dict[str, SearchResult] = {}
    for f in findings:
        for s in f.sources:
            if s.url and s.url not in seen:
                seen[s.url] = s
    sources = list(seen.values())

    findings_text = "\n\n".join(
        f"### {f.sub_question}\nConfidence: {f.confidence}  Cutoff: {f.info_cutoff or 'unknown'}\n{f.answer}"
        for f in findings
    )
    src_index = "\n".join(f"[{i+1}] {s.title} — {s.url}" for i, s in enumerate(sources))
    prompt = f"Topic: {topic}\n\nFindings:\n{findings_text}\n\nSource index:\n{src_index}"
    parsed = llm.complete_json(prompt, system=SYNTH_SYSTEM, max_tokens=4096)

    return ResearchReport(
        topic=topic,
        summary=parsed["summary"],
        sections=parsed["sections"],
        sources=sources,
        process_log=[],
        open_questions=parsed.get("open_questions", []),
        key_conclusions=parsed.get("key_conclusions", []),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )