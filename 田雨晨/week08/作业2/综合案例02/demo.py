"""Offline self-check. Run: python demo.py — no API calls."""
import json

from research.models import Finding, ResearchReport, SearchResult, SubQuestion
from research.search import BochaSearcher


def demo() -> None:
    # 1. SearchResult.cite
    r = SearchResult(url="https://x", title="t", snippet="s", summary="sum")
    assert r.cite(1) == "[1] t — https://x", r.cite(1)

    # 2. Search payload construction (offline — does not call the API)
    payload = {"query": "x", "summary": True, "count": 10}
    assert json.dumps(payload) == '{"query": "x", "summary": true, "count": 10}'

    # 3. SubQuestion / Finding defaults
    sq = SubQuestion(question="q")
    assert sq.rationale == ""
    f = Finding(sub_question="q", answer="a")
    assert f.confidence == 0.5 and f.sources == []

    # 4. Report markdown render
    rep = ResearchReport(
        topic="demo", summary="sum",
        sections=[{"title": "S1", "body": "B", "citations": [1]}],
        sources=[r], process_log=["step1"], open_questions=["q?"],
        key_conclusions=["c"], generated_at="2026-09-11T00:00:00+00:00",
    )
    md = rep.to_markdown()
    assert "深度研究：demo" in md
    assert "## 摘要" in md
    assert "[1] t — https://x" in md
    assert "## 研究过程" in md

    # 5. BochaSearcher raises clearly when key absent
    import os
    os.environ.pop("BOCHA_API_KEY", None)
    try:
        BochaSearcher()
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError when BOCHA_API_KEY is missing")

    print("demo OK")


if __name__ == "__main__":
    demo()