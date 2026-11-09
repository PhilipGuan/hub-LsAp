"""Sub-question + search results → Finding (answer + confidence + cutoff)."""
from .llm import LLM
from .models import Finding, SearchResult

EXTRACTOR_SYSTEM = """You extract a precise answer to a sub-question from the provided search results.
- Cite sources inline using [n] matching the numbered list provided.
- If evidence is thin or conflicting, say so explicitly.
- End your answer with a one-line confidence score (0.0 - 1.0) and, if known, the information cutoff date.
Return JSON: {"answer": "...", "confidence": 0.0, "info_cutoff": "YYYY-MM-DD or empty"}
No prose, no markdown fence."""


def extract_answer(sub_question: str, results: list[SearchResult], llm: LLM) -> Finding:
    if not results:
        return Finding(
            sub_question=sub_question,
            answer="未检索到来源,结论为模型推断,不可作为决策依据。",
            confidence=0.0,
        )
    numbered = "\n".join(
        f"[{i+1}] {r.title}\nURL: {r.url}\n"
        f"Summary: {(r.summary or r.snippet)[:1500]}"
        for i, r in enumerate(results)
    )
    prompt = f"Sub-question: {sub_question}\n\nSources:\n{numbered}"
    parsed = llm.complete_json(prompt, system=EXTRACTOR_SYSTEM)
    return Finding(
        sub_question=sub_question,
        answer=parsed["answer"],
        sources=results,
        confidence=float(parsed.get("confidence", 0.5)),
        info_cutoff=parsed.get("info_cutoff", ""),
    )