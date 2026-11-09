"""编排引擎 —— 整个项目唯一的真相源。

**刻意不引入 LangGraph / LangChain。** 全流程只有 5 个节点加一个循环，图抽象
带来的间接层是净负担；而写成异步生成器让 CLI 和 SSE 共用同一份实现：

    CLI :  async for ev in run_research(req): 打印 ev
    SSE :  async for ev in run_research(req): yield ev.to_sse()

改流程只改这一个文件，两边不会跑偏。

状态流转：

    INIT → PLANNING → SEARCHING → FETCHING → READING → REFLECTING
                                        ↑                    │
                                        └──── 继续则回到 SEARCHING
                                                             ↓
                                                     SYNTHESIZING → DONE

**收尾保护**：即便一条可用来源都没拿到，也会出一份报告 —— 全文标注模型推断、
置信度 low、遗留问题里写明「本次未能获取可用来源」。**不产出报告是更差的体验。**
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from datetime import datetime

from .agents.planner import plan
from .agents.prompts import render_sub_questions
from .agents.reader import read_many
from .agents.reflector import ReflectionOutcome, reflect
from .agents.synthesizer import synthesize
from .citation import (
    INFERRED_PREFIX,
    CitationStats,
    check_text,
    merge_stats,
    process_text,
    split_sentences,
)
from .confidence import compute_confidence
from .config import Settings
from .config import settings as default_settings
from .dedup import DedupeIndex, shingle_jaccard
from .events import Event, EventType, make_event
from .fetch import Fetcher, make_source
from .llm import LLMClient
from .models import (
    ContentOrigin,
    FetchStatus,
    Note,
    ProcessRound,
    ProcessSummary,
    QueryRecord,
    ReflectionRecord,
    ResearchRequest,
    ResearchState,
    RunStatus,
    Source,
    SourceRef,
)
from .report import build_source_refs, render_markdown
from .search import BochaSearchClient
from .stopping import StopDecision, decide, programmatic_coverage

logger = logging.getLogger(__name__)


class _Emitter:
    """包一层序号自增，免得每个 yield 都要手写 seq。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.seq = 0

    def make(self, event_type: EventType, *, round_no: int = 0, **payload) -> Event:
        self.seq += 1
        return make_event(
            event_type, run_id=self.run_id, round_no=round_no, seq=self.seq, **payload
        )


def _unsourced_ratio_proxy(notes: list[Note]) -> float:
    """反思阶段用的「无来源占比」近似值。

    这个时点还没有结论，只有笔记，所以拿「弱证据笔记」（来自降级来源的）占比
    作为近似 —— 它们就是将来最可能变成无来源结论的那批。真正的结论级比例
    在综合完成后由 ``compute_confidence`` 算。
    """
    if not notes:
        return 1.0
    return len([n for n in notes if n.weak]) / len(notes)


async def run_research(
    request: ResearchRequest,
    *,
    settings: Settings | None = None,
    run_id: str | None = None,
    searcher: BochaSearchClient | None = None,
    llm: LLMClient | None = None,
    fetcher: Fetcher | None = None,
) -> AsyncIterator[Event]:
    """跑完一次完整研究，边跑边吐事件。

    依赖可注入 —— 测试时传假的 searcher / llm / fetcher 就能把整条链路跑穿，
    不需要联网。传 None 则各自建默认实例并负责关闭。
    """
    cfg = settings or default_settings
    emitter = _Emitter(run_id or uuid.uuid4().hex[:12])

    state = ResearchState(run_id=emitter.run_id, request=request)
    started = time.monotonic()

    owns_searcher = searcher is None
    owns_llm = llm is None
    owns_fetcher = fetcher is None

    searcher = searcher or BochaSearchClient(cfg)
    llm = llm or LLMClient(cfg)
    fetcher = fetcher or Fetcher(cfg)

    index = DedupeIndex()
    stalled_rounds = 0

    try:
        yield emitter.make(
            EventType.run_started,
            topic=request.topic,
            max_rounds=request.max_rounds,
            fetch_enabled=request.fetch_enabled,
            started_at=started,
        )

        # ══ PLANNING ════════════════════════════════════════
        state.status = RunStatus.planning
        yield emitter.make(EventType.status, status=state.status.value, message="正在拆解子问题")

        plan_result = await plan(
            request.topic,
            llm,
            max_sub_questions=cfg.queries_per_round + 1,
            queries_per_round=cfg.queries_per_round,
        )
        state.sub_questions = plan_result.sub_questions

        yield emitter.make(
            EventType.plan,
            sub_questions=[
                {"qid": q.qid, "text": q.text, "rationale": q.rationale}
                for q in plan_result.sub_questions
            ],
            queries=plan_result.queries,
            rationale=plan_result.rationale,
            degraded=plan_result.degraded,
        )

        next_queries = plan_result.queries

        # ══ 多轮循环 ════════════════════════════════════════
        while state.round < request.max_rounds:
            state.round += 1
            round_no = state.round
            round_start_sources = len(state.sources)
            round_queries: list[str] = []
            queries_ok = 0

            # ── SEARCHING ───────────────────────────────────
            state.status = RunStatus.searching
            yield emitter.make(
                EventType.status,
                round_no=round_no,
                status=state.status.value,
                message=f"第 {round_no} 轮检索：{len(next_queries)} 个关键词",
            )

            for query in next_queries:
                if index.is_duplicate_query(query):
                    state.queries.append(
                        QueryRecord(
                            query_id=f"Q{len(state.queries) + 1}",
                            text=query,
                            round=round_no,
                            reason="skipped_duplicate",
                            ok=True,
                            result_count=0,
                        )
                    )
                    yield emitter.make(
                        EventType.search,
                        round_no=round_no,
                        query=query,
                        skipped=True,
                        reason="与已用关键词过于相似，跳过",
                    )
                    continue

                index.register_query(query)
                round_queries.append(query)

                outcome = await searcher.search(
                    query, count=cfg.search_count_per_query, summary=True
                )

                record = QueryRecord(
                    query_id=f"Q{len(state.queries) + 1}",
                    text=query,
                    round=round_no,
                    reason="initial_plan" if round_no == 1 else "reflect_gap",
                    ok=outcome.ok,
                    code=outcome.code,
                    error=outcome.error,
                    result_count=len(outcome.results),
                    latency_ms=outcome.latency_ms,
                )

                if outcome.ok:
                    queries_ok += 1
                    new_count = _ingest_results(
                        outcome.results, state, index, round_no, query, cfg, emitter
                    )
                    record.new_source_count = new_count
                else:
                    yield emitter.make(
                        EventType.error,
                        round_no=round_no,
                        scope="search",
                        query=query,
                        message=outcome.error or "检索失败",
                        fatal=False,
                    )

                state.queries.append(record)
                yield emitter.make(
                    EventType.search_results,
                    round_no=round_no,
                    query=query,
                    ok=outcome.ok,
                    found=len(outcome.results),
                    added=record.new_source_count,
                    latency_ms=outcome.latency_ms,
                )

            # ── FETCHING ────────────────────────────────────
            # 只抓本轮新增的；老来源上一轮已经抓过了（成功或失败都记在案）
            pending = [s for s in state.sources[round_start_sources:] if not s.fetched]

            if pending and request.fetch_enabled:
                state.status = RunStatus.fetching
                yield emitter.make(
                    EventType.status,
                    round_no=round_no,
                    status=state.status.value,
                    message=f"抓取 {len(pending)} 个页面",
                )
                await fetcher.fetch_many(pending)

                for source in pending:
                    # 判据是**抓取状态**而不是「能不能引用」：一个被反爬挡下、
                    # 降级用搜索摘要的来源仍然是「没抓到」，必须如实报出来。
                    # 拿 readable 当判据会把这些降级粉饰成成功。
                    if source.fetch_status is FetchStatus.ok:
                        yield emitter.make(
                            EventType.fetch,
                            round_no=round_no,
                            sid=source.sid,
                            status=source.fetch_status.value,
                            origin=source.content_origin.value,
                            chars=source.content_chars,
                            url=source.url,
                            extractor=source.extractor,
                        )
                    else:
                        yield emitter.make(
                            EventType.fetch_failed,
                            round_no=round_no,
                            sid=source.sid,
                            status=source.fetch_status.value,
                            url=source.url,
                            error=source.fetch_error,
                            fallback=source.content_origin.value,
                            usable=source.readable,
                            message=(
                                "已降级为搜索摘要/片段（仍可引用）"
                                if source.readable
                                else "无可用内容，不会进入笔记"
                            ),
                        )

                _resolve_content_duplicates(pending, state, index)
            elif pending:
                for source in pending:
                    source.fetched = True

            # ── READING ─────────────────────────────────────
            state.status = RunStatus.reading
            readable_now = [s for s in state.sources[round_start_sources:] if s.readable]
            yield emitter.make(
                EventType.status,
                round_no=round_no,
                status=state.status.value,
                message=f"阅读 {len(readable_now)} 个来源",
            )

            if readable_now:
                notes = await read_many(
                    readable_now,
                    render_sub_questions(state.sub_questions),
                    llm,
                    round_no=round_no,
                )
                _assign_note_qids(notes, state)
                state.notes.extend(notes)

                for note in notes:
                    yield emitter.make(
                        EventType.note,
                        round_no=round_no,
                        note_id=note.note_id,
                        sid=note.sid,
                        qid=note.qid,
                        claim=note.claim,
                        weak=note.weak,
                    )

            # ── REFLECTING ──────────────────────────────────
            state.status = RunStatus.reflecting
            yield emitter.make(
                EventType.status,
                round_no=round_no,
                status=state.status.value,
                message="判断是否需要补充检索",
            )

            reflection = await reflect(
                request.topic,
                state.sub_questions,
                state.notes,
                index.used_queries,
                llm,
                round_no=round_no,
                max_rounds=request.max_rounds,
            )

            coverage = programmatic_coverage(state.sub_questions, state.notes, state.valid_sids)
            _apply_coverage(state, coverage)

            new_effective = len(state.readable_sources) - _readable_count_at(
                state, round_start_sources
            )
            stalled_rounds = 0 if new_effective > 0 else stalled_rounds + 1

            decision = decide(
                round_no=round_no,
                max_rounds=request.max_rounds,
                reflection=reflection,
                coverage=coverage,
                sub_questions=state.sub_questions,
                stalled_rounds=stalled_rounds,
                unsourced_ratio=_unsourced_ratio_proxy(state.notes),
                sources=state.sources,
                queries_ok=queries_ok,
                queries_total=len(round_queries),
                elapsed_s=time.monotonic() - started,
                settings=cfg,
            )

            state.reflections.append(_reflection_record(round_no, reflection, coverage, decision))

            yield emitter.make(
                EventType.reflect,
                round_no=round_no,
                llm_sufficient=reflection.sufficient,
                coverage=coverage,
                gaps=[g.get("gap", "") for g in reflection.gaps],
                next_queries=[q.get("query", "") for q in reflection.next_queries],
                rationale=reflection.rationale,
                decision="stop" if decision.should_stop else "continue",
                stop_reason=decision.reason.value if decision.reason else None,
                detail=decision.detail,
                degraded=reflection.degraded,
            )

            yield emitter.make(
                EventType.round_end,
                round_no=round_no,
                new_sources=len(state.sources) - round_start_sources,
                new_readable=new_effective,
                total_sources=len(state.sources),
                total_notes=len(state.notes),
            )

            if decision.should_stop:
                state.stop_reason = decision.reason.value if decision.reason else None
                break

            next_queries = [q["query"] for q in reflection.next_queries if q.get("query")][
                : cfg.queries_per_round
            ]

            if not next_queries and not decision.should_stop:
                # 走到了这里说明程序不同意收尾，可 LLM 又没给出下一步搜什么 ——
                # 直接停的话，程序否决权就只是句空话。退而求其次：
                # 拿覆盖率最低的那几个子问题原文当检索词，好歹把缺口再试一次。
                # 注意条件是「程序判定要继续」而不是「LLM 说不够」：
                # LLM 说够、程序否决，正是最需要兜底的那种情况。
                next_queries = _queries_for_gaps(state, coverage, index, cfg)
                if next_queries:
                    yield emitter.make(
                        EventType.search,
                        round_no=round_no,
                        skipped=False,
                        reason="反思未给出检索词，改用手边缺口子问题兜底",
                        queries=next_queries,
                    )

            if not next_queries:
                state.stop_reason = "no_new_sources"
                yield emitter.make(
                    EventType.stop,
                    round_no=round_no,
                    reason=state.stop_reason,
                    detail="没有可继续的检索方向，提前收尾",
                )
                break

        # ══ SYNTHESIZING ════════════════════════════════════
        state.status = RunStatus.synthesizing
        yield emitter.make(
            EventType.synthesize_start,
            status=state.status.value,
            sources=len(state.readable_sources),
            notes=len(state.notes),
            message="综合材料并生成报告",
        )

        report = await synthesize(state, llm)

        for section in report.sections:
            yield emitter.make(
                EventType.section,
                qid=section.qid or "",
                heading=section.heading,
                sids=section.citation_sids,
            )

        stats = _finalize(state, report, cfg)
        state.report = report
        state.confidence = report.confidence

        yield emitter.make(
            EventType.confidence,
            score=report.confidence.overall_score,
            level=report.confidence.level,
            caps=report.confidence.factors.caps_applied,
            raw=report.confidence.factors.raw,
        )
        yield emitter.make(
            EventType.report,
            title=report.title,
            sections=len(report.sections),
            conclusions=len(report.key_conclusions),
            sources=len(report.sources),
            markdown=report.markdown,
            citation_stats=stats.as_dict(),
        )

        state.status = RunStatus.done
        state.finished_at = datetime.now()
        yield emitter.make(
            EventType.stop,
            reason=state.stop_reason or "sufficient",
            rounds=state.round,
            elapsed_s=round(time.monotonic() - started, 1),
        )
        yield emitter.make(
            EventType.done,
            status=state.status.value,
            rounds=state.round,
            sources=len(state.sources),
            notes=len(state.notes),
            stop_reason=state.stop_reason,
            report=report.model_dump(mode="json"),
        )

    except asyncio.CancelledError:
        state.status = RunStatus.cancelled
        state.stop_reason = "cancelled"
        yield emitter.make(EventType.cancelled, message="研究已取消")
        raise
    except Exception as exc:  # noqa: BLE001 - 引擎不能把异常漏给调用方
        logger.exception("研究过程发生未预期错误")
        state.status = RunStatus.failed
        state.errors.append({"stage": state.status.value, "error": str(exc)})
        yield emitter.make(
            EventType.error,
            scope="engine",
            message=f"{exc.__class__.__name__}: {exc}",
            fatal=True,
        )
    finally:
        if owns_searcher:
            await searcher.aclose()
        if owns_llm:
            await llm.aclose()
        if owns_fetcher:
            await fetcher.aclose()


# ══════════════════════════════════════════════════════════════
# 内部步骤
# ══════════════════════════════════════════════════════════════


def _ingest_results(
    results: list,
    state: ResearchState,
    index: DedupeIndex,
    round_no: int,
    query: str,
    cfg: Settings,
    emitter: _Emitter,
) -> int:
    """把一轮检索结果并进 state，返回新增来源数。"""
    added = 0

    for item in results:
        if len(state.sources) >= cfg.max_sources:
            break
        if not item.url:
            continue

        existing = index.find_url_duplicate(item.url, item.title)
        if existing is not None:
            kept = state.source_by_sid(existing)
            if kept is not None and query not in kept.found_by_queries:
                kept.found_by_queries.append(query)
            continue

        source = make_source(
            sid=index.next_sid(),
            url=item.url,
            title=item.title,
            site_name=item.site_name,
            snippet=item.snippet,
            search_summary=item.summary or None,
            published_at=item.published_at,
            round_no=round_no,
            query=query,
        )
        index.register(source)
        state.sources.append(source)
        added += 1

    return added


def _resolve_content_duplicates(
    batch: list[Source], state: ResearchState, index: DedupeIndex
) -> None:
    """抓取完成后判定转载 / 镜像，并尝试把更好的正文迁移给保留者。"""
    for source in batch:
        if not source.content:
            continue
        duplicate_of = index.find_content_duplicate(source)
        if duplicate_of is None or duplicate_of == source.sid:
            continue

        kept = state.source_by_sid(duplicate_of)
        if kept is None:
            continue

        if index.promote_content(kept, source):
            logger.debug("把 %s 的正文迁移给了保留者 %s", source.sid, kept.sid)

        source.duplicate_of = duplicate_of
        logger.debug("%s 判定为 %s 的重复（转载/镜像）", source.sid, duplicate_of)


def _queries_for_gaps(
    state: ResearchState,
    coverage: dict[str, float],
    index: DedupeIndex,
    cfg: Settings,
) -> list[str]:
    """反思没给出下一轮检索词时，拿覆盖率最低的子问题原文顶上。

    覆盖率升序排，只取还没被搜过的词。这是兜底不是主力 —— 正常情况下
    反思会给出比子问题原文精准得多的检索词。
    """
    ranked = sorted(state.sub_questions, key=lambda q: coverage.get(q.qid, 0.0))
    queries: list[str] = []
    for sub in ranked:
        if index.is_duplicate_query(sub.text):
            continue
        queries.append(sub.text)
        if len(queries) >= cfg.queries_per_round:
            break
    return queries


def _assign_note_qids(notes: list[Note], state: ResearchState) -> None:
    """reader 不产出 qid —— 这里按笔记内容与子问题的重合度就近归类。

    reader 一次看的是**所有**子问题，所以它没法说这条笔记属于哪一个。
    用字符重合度做粗归类：归类错了也不致命，只是报告分节时材料放错位置。
    """
    if not state.sub_questions:
        return

    for note in notes:
        best_qid = state.sub_questions[0].qid
        best_score = -1.0
        for sub in state.sub_questions:
            score = shingle_jaccard(note.claim, sub.text, size=2)
            if score > best_score:
                best_score = score
                best_qid = sub.qid
        note.qid = best_qid


def _apply_coverage(state: ResearchState, coverage: dict[str, float]) -> None:
    for sub in state.sub_questions:
        score = coverage.get(sub.qid, 0.0)
        sub.coverage = score
        if score >= 0.6:
            sub.status = "covered"
        elif score > 0:
            sub.status = "in_progress"
        else:
            sub.status = "insufficient"


def _readable_count_at(state: ResearchState, source_index: int) -> int:
    """在第 ``source_index`` 条来源之前，已经有多少条可引用来源。"""
    return len([s for s in state.sources[:source_index] if s.readable])


def _reflection_record(
    round_no: int,
    reflection: ReflectionOutcome,
    coverage: dict[str, float],
    decision: StopDecision,
) -> ReflectionRecord:
    return ReflectionRecord(
        round=round_no,
        llm_sufficient=reflection.sufficient,
        decision="stop" if decision.should_stop else "continue",
        stop_reason=decision.reason.value if decision.reason else None,
        coverage=coverage,
        gaps=reflection.gaps,
        next_queries=reflection.next_queries,
        rationale=reflection.rationale,
        new_valid_sources=0,
    )


def _finalize(state: ResearchState, report, cfg: Settings):
    """收尾：校验引用 → 回填来源 → 算置信度 → 渲染 markdown。"""
    valid_sids = state.valid_sids

    # 关键结论里的编号也要过一遍校验
    conclusion_texts: list[str] = []
    for conclusion in report.key_conclusions:
        cleaned, _ = process_text(conclusion.text, valid_sids)
        conclusion.text = cleaned
        conclusion.domain_count = len(
            {
                s.domain or s.url
                for s in (state.source_by_sid(sid) for sid in conclusion.sids)
                if s is not None
            }
        )
        conclusion_texts.append(conclusion.text)

    # 被引用的来源集合 = 正文里出现过的 + 结论里列出的
    cited_sids: set[str] = set()
    section_map: dict[str, list[str]] = {}
    for section in report.sections:
        for sid in section.citation_sids:
            cited_sids.add(sid)
            section_map.setdefault(sid, []).append(section.heading)
    for conclusion in report.key_conclusions:
        cited_sids.update(conclusion.sids)

    # 来源列表：**全部**来源都列出来（含被降级、被判重的），如实标注状态
    note_counts = Counter(n.sid for n in state.notes)
    report.sources = build_source_refs(
        [
            SourceRef(
                sid=s.sid,
                title=s.title,
                url=s.url,
                site_name=s.site_name,
                domain=s.domain,
                published_at=s.published_at,
                fetched=s.fetched,
                fetch_status=s.fetch_status,
                content_origin=s.content_origin,
                note_count=note_counts.get(s.sid, 0),
                readable=s.readable,
            )
            for s in state.sources
        ],
        cited_sids,
        section_map,
    )

    # 正文统计 + 结论统计合成一份报告级引用健康度
    stats = merge_stats([_text_stats(s.body_md, valid_sids) for s in report.sections])

    unsourced = len([c for c in report.key_conclusions if not c.sids])
    report.confidence = compute_confidence(
        state.sources,
        report.key_conclusions,
        cited_sids=cited_sids,
        unsourced_conclusion_count=unsourced,
        total_conclusion_count=len(report.key_conclusions),
        inferred_statements=[c.text for c in report.key_conclusions if not c.sids],
        limitations=report.confidence.limitations,
        settings=cfg,
    )

    report.process = _build_process(state)
    report.as_of = report.confidence.as_of
    report.generated_at = datetime.now()

    report.citation_stats = {
        **stats.as_dict(),
        "unsourced_conclusions": unsourced,
        "total_conclusions": len(report.key_conclusions),
        "cited_sources": len(cited_sids & valid_sids),
        "valid_sources": len(valid_sids),
    }
    report.markdown = render_markdown(report)
    return stats


def _text_stats(text: str, valid_sids: set[str]) -> CitationStats:
    """分节正文的引用健康度。

    ``synthesizer`` 生成时已经跑过一遍 ``process_text``，这里只做统计不再改写 ——
    再跑一次标注没有意义，而且会把已经标好的句子重复计数。
    """
    stats = check_text(text, valid_sids)
    stats.sentences = len([s for s in split_sentences(text) if s.strip()])
    stats.inferred_sentences = text.count(INFERRED_PREFIX)
    return stats


def _build_process(state: ResearchState) -> ProcessSummary:
    timeline: list[ProcessRound] = []
    for reflection in state.reflections:
        round_queries = [q.text for q in state.queries if q.round == reflection.round]
        timeline.append(
            ProcessRound(
                round=reflection.round,
                queries=round_queries,
                found=sum(q.result_count for q in state.queries if q.round == reflection.round),
                read=len([s for s in state.sources if s.first_seen_round == reflection.round]),
                newly_added=sum(
                    q.new_source_count for q in state.queries if q.round == reflection.round
                ),
                gaps=[g.get("gap", "") for g in reflection.gaps],
                rationale=reflection.rationale,
                decision=reflection.decision,
            )
        )

    urls_read = [
        {
            "sid": s.sid,
            "url": s.url,
            "chars": s.content_chars,
            "origin": s.content_origin.value,
        }
        for s in state.sources
        if s.content_origin is ContentOrigin.page
    ]

    return ProcessSummary(
        rounds=len(state.reflections),
        total_queries=len([q for q in state.queries if q.reason != "skipped_duplicate"]),
        total_sources_found=len(state.sources),
        total_sources_read=len(state.readable_sources),
        total_notes=len(state.notes),
        timeline=timeline,
        urls_read=urls_read,
    )


__all__ = ["run_research"]
