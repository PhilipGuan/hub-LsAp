"""研究编排：keyword 拆题 → 多轮 search → summary 抽事实 → judge 判充分 → report 成文。"""
from __future__ import annotations

import logging
import re
import time

from backend import config
from backend.agent.judge import JudgeAgent
from backend.agent.keyword import KeywordAgent
from backend.agent.report import ReportAgent
from backend.agent.summary import SummaryAgent
from backend.models import (
    ProcessStep,
    ResearchReport,
    ResearchTask,
    SearchResult,
    Source,
    SubQuestion,
)
from backend import storage
from backend.tools import bocha_search, dedup

logger = logging.getLogger(__name__)

_REF = re.compile(r"\[(\d+)\]")


class _SourcePool:
    """全局来源池：URL 去重，维护 [n] 编号。"""

    def __init__(self) -> None:
        self.sources: list[Source] = []
        self._url_to_idx: dict[str, int] = {}

    def add(self, r: SearchResult, used_in: str) -> int:
        """返回 1-based 全局编号。"""
        key = r.url.rstrip("/")
        if key in self._url_to_idx:
            idx = self._url_to_idx[key]
            if used_in and used_in not in self.sources[idx - 1].used_in:
                self.sources[idx - 1].used_in.append(used_in)
            return idx
        idx = len(self.sources) + 1
        self.sources.append(
            Source(
                id=f"s{idx}",
                title=r.title,
                url=r.url,
                site_name=r.site_name,
                snippet=(r.text or "")[:300],
                used_in=[used_in] if used_in else [],
            )
        )
        self._url_to_idx[key] = idx
        return idx

    def remap(self, facts: list[str], local_results: list[SearchResult], sub_q: str) -> list[str]:
        """把事实里的局部引用 [n] 改写成全局来源编号，保证可追溯。"""
        local_to_global: dict[int, int] = {}
        for i, r in enumerate(local_results, 1):
            local_to_global[i] = self.add(r, sub_q)

        def repl(m: re.Match) -> str:
            n = int(m.group(1))
            if n in local_to_global:
                return f"[{local_to_global[n]}]"
            return f"[{n}]"

        return [_REF.sub(repl, f) for f in facts]


def run_research(
    topic: str,
    task_id: str | None = None,
    max_rounds: int | None = None,
    fetch_full: bool = True,
) -> ResearchTask:
    """执行一次完整研究。同步阻塞，由调用方放进后台线程。"""
    started = time.time()
    rounds_limit = max_rounds or config.RESEARCH_MAX_ROUNDS
    task = ResearchTask(id=task_id, topic=topic) if task_id else ResearchTask(topic=topic)
    task.status = "running"
    task.stage = "keyword"
    task.progress = 2
    storage.save(task)

    process: list[ProcessStep] = []
    pool = _SourcePool()
    keyword_agent = KeywordAgent()
    summary_agent = SummaryAgent()
    judge_agent = JudgeAgent()
    report_agent = ReportAgent()

    def step(round_no: int, action: str, detail: str) -> None:
        process.append(ProcessStep(round=round_no, action=action, detail=detail))
        task.stage = action
        task.touch()
        storage.save(task)
        logger.info("第%s轮 %s：%s", round_no, action, detail[:80])

    # ---- 1. keyword：拆子问题 + 关键词 ----
    sub_questions: list[SubQuestion] = keyword_agent.run(topic)
    step(1, "keyword", f"拆出 {len(sub_questions)} 个子问题：" + "；".join(s.question for s in sub_questions))

    # ---- 2. 多轮 search → summary → judge ----
    round_no = 0
    while round_no < rounds_limit:
        round_no += 1
        pending = [s for s in sub_questions if s.status == "pending"]
        if not pending:
            break
        task.rounds_used = round_no
        task.progress = min(85, 10 + int(70 * (round_no - 1) / max(rounds_limit, 1)))

        for sq in pending:
            hits: list[SearchResult] = []
            for kw in sq.keywords:
                hits.extend(bocha_search(kw))
                if kw not in task.keywords_used:
                    task.keywords_used.append(kw)
            hits = dedup(hits)[: config.SEARCH_COUNT]
            if not hits:
                sq.status = "insufficient"
                sq.facts.append("（该子问题未检索到有效结果）")
                step(round_no, "search", f"「{sq.question}」无结果")
                continue

            step(round_no, "search", f"「{sq.question}」检索 {len(hits)} 条，关键词：{'/'.join(sq.keywords)}")

            facts, gaps = summary_agent.run(
                sq.question,
                hits,
                fetch_full=fetch_full,
                max_fetch=config.MAX_FETCH_PER_ROUND,
            )
            facts = pool.remap(facts, hits, sq.question)
            sq.facts.extend(facts)
            sq.source_ids = list({f"s{x}" for x in _REF.findall(' '.join(facts)) if x.isdigit()})
            step(round_no, "summary", f"「{sq.question}」抽取 {len(facts)} 条事实")

            sufficient, reason, followups = judge_agent.judge_sufficiency(sq.question, sq.facts, gaps)
            step(round_no, "judge", f"「{sq.question}」{'充分' if sufficient else '需补检'}：{reason}")
            if sufficient or round_no >= rounds_limit:
                sq.status = "done"
            else:
                sq.keywords = followups or [sq.question]

    # ---- 3. report：综合成报告 ----
    task.progress = 90
    sub_facts = [{"question": s.question, "facts": s.facts} for s in sub_questions]
    task.pages_read = [s.url for s in pool.sources]
    step(round_no, "report", f"汇总 {len(pool.sources)} 条来源，生成报告")

    report: ResearchReport = report_agent.run(topic, sub_facts, pool.sources, round_no)
    report.process = process
    report.rounds_used = round_no
    report.elapsed_sec = round(time.time() - started, 1)

    task.report = report
    task.status = "completed"
    task.stage = "done"
    task.progress = 100
    task.touch()
    storage.save(task)
    logger.info("研究完成：%s，用时 %.1fs，来源 %s 条", topic, time.time() - started, len(pool.sources))
    return task


def run_research_safe(topic: str, task_id: str | None = None, **kw) -> ResearchTask:
    """包一层异常，保证任务状态落到 failed 而不是卡在 running。"""
    task = ResearchTask(id=task_id, topic=topic) if task_id else ResearchTask(topic=topic)
    try:
        return run_research(topic, task_id=task.id, **kw)
    except Exception as exc:  # noqa: BLE001
        logger.exception("研究失败：%s", exc)
        loaded = storage.load(task.id) or task
        loaded.status = "failed"
        loaded.error = str(exc)
        loaded.stage = "failed"
        loaded.touch()
        storage.save(loaded)
        return loaded


if __name__ == "__main__":
    from backend.config import setup_logging

    setup_logging()
    t = run_research_safe("2025-2026年国内新能源车企竞争格局", max_rounds=2)
    print("\n=== 状态 ===")
    print("status:", t.status, "| rounds:", t.rounds_used, "| 来源数:", len(t.report.sources) if t.report else 0)
    print("关键词：", t.keywords_used)
    if t.report:
        print("\n=== Markdown 报告（前 1500 字）===\n")
        print(t.report.to_markdown()[:1500])
