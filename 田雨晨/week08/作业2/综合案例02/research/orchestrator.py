"""Top-level research pipeline: plan → search → extract → synthesize."""
from .extractor import extract_answer
from .llm import LLM
from .models import Finding, ResearchReport
from .planner import plan_sub_questions
from .search import BochaSearcher
from .synthesizer import synthesize


def research(
    topic: str,
    searcher: BochaSearcher,
    llm: LLM,
    per_question_count: int = 8,
) -> ResearchReport:
    sub_qs = plan_sub_questions(topic, llm)
    log = [f"规划：拆为 {len(sub_qs)} 个子问题"]
    findings: list[Finding] = []
    for i, sq in enumerate(sub_qs, 1):
        results = searcher.search(sq.question, count=per_question_count)
        log.append(f"[{i}/{len(sub_qs)}] 检索「{sq.question}」→ {len(results)} 条结果")
        findings.append(extract_answer(sq.question, results, llm))
    report = synthesize(topic, findings, llm)
    report.process_log = log + report.process_log
    return report