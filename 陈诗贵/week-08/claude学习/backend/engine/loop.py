"""agentic 研究循环：规划 → 多轮检索 → 阅读抽取 → 判断补检 → 综合。

这是深度研究助手的核心：区别于一次性问答，它在信息不足时会主动补检。
"""
from __future__ import annotations

import logging

from .. import config
from ..agent import llm, prompts
from ..models import (
    Confidence,
    Evidence,
    ResearchRecord,
    SearchRound,
    Section,
    Source,
    SubQuestion,
    make_slug,
)
from ..tools import search as search_tool

logger = logging.getLogger(__name__)


async def run(topic: str, record_id: str | None = None) -> ResearchRecord:
    """对给定主题执行完整研究，返回填好的 ResearchRecord（不含落盘）。"""
    record = ResearchRecord(id=record_id or make_slug(topic), topic=topic)
    process = record.process

    # 1. 规划：拆子问题
    logger.info("【规划】拆分子问题…")
    plan_result = await llm.chat_json(prompts.plan(topic))
    process.sub_questions = [
        SubQuestion(**sq) for sq in plan_result.get("sub_questions", [])
    ]
    logger.info(
        "拆出 %d 个子问题：%s",
        len(process.sub_questions),
        [sq.question for sq in process.sub_questions],
    )

    # 2. 多轮检索 + 抽取 + 补检
    pending_queries = [sq.question for sq in process.sub_questions]
    all_evidences: list[Evidence] = []
    round_no = 0

    while pending_queries and round_no < config.MAX_ROUNDS:
        round_no += 1
        purpose = "首轮检索" if round_no == 1 else "补检"
        logger.info("【检索】第 %d 轮（%s），%d 个关键词", round_no, purpose, len(pending_queries))

        search_round = SearchRound(
            round_no=round_no, purpose=purpose, queries=list(pending_queries)
        )

        for query in pending_queries:
            sources = await search_tool.search(query)
            if not sources:
                logger.warning("关键词「%s」未检索到来源，跳过抽取", query)
                continue
            offset = len(record.sources)
            record.sources.extend(sources)
            search_round.pages_read.extend(sources)

            logger.info("  抽取：%s（%d 个来源）", query, len(sources))
            numbered = _number_sources(sources, offset)
            try:
                extract_result = await llm.chat_json(prompts.extract(query, numbered))
            except Exception as exc:
                logger.error("  抽取失败：%s", exc)
                continue
            for ev in extract_result.get("evidences", []):
                si = ev.get("source_index")
                # 校验：source_index 必须落在本轮来源编号范围内，越界则视为模型推断
                valid = isinstance(si, int) and 0 <= si < len(sources)
                global_si = offset + si if valid else None
                evidence = Evidence(
                    claim=ev.get("claim", ""),
                    source_index=global_si,
                    quote=ev.get("quote", ""),
                )
                all_evidences.append(evidence)
                search_round.extracted.append(evidence)

        process.rounds.append(search_round)
        process.iterations = round_no

        # 3. 补检判定
        if round_no >= config.MAX_ROUNDS:
            logger.info("已达检索轮次上限 %d，停止检索", config.MAX_ROUNDS)
            break

        logger.info("【补检判定】…")
        dump = _dump_evidence(record.sources, all_evidences)
        try:
            decision = await llm.chat_json(prompts.decide(topic, dump, round_no))
        except Exception as exc:
            logger.error("补检判定失败，默认结束检索：%s", exc)
            break
        search_round.decision = decision.get("reason", "")
        new_queries = [q for q in decision.get("new_queries", []) if q]

        if decision.get("need_more") and new_queries:
            logger.info("信息不足，补检：%s", decision.get("reason"))
            pending_queries = new_queries
        else:
            logger.info("信息已充分，结束检索")
            break

    # 4. 综合生成报告
    logger.info("【综合】生成报告…")
    dump = _dump_evidence(record.sources, all_evidences)
    synth = await llm.chat_json(prompts.synthesize(topic, dump))

    record.summary = synth.get("summary", "")
    record.sections = [Section(**s) for s in synth.get("sections", [])]
    record.key_conclusions = synth.get("key_conclusions", [])
    record.open_questions = synth.get("open_questions", [])
    conf = synth.get("confidence", {})
    record.confidence = Confidence(
        level=conf.get("level", "medium"),
        cutoff=conf.get("cutoff", ""),
        notes=conf.get("notes", []),
    )

    logger.info(
        "研究完成：%d 个来源、%d 条证据、%d 轮检索",
        len(record.sources),
        len(all_evidences),
        process.iterations,
    )
    return record


def _number_sources(sources: list[Source], offset: int) -> str:
    """把来源列表格式化为带编号的文本，供抽取使用。"""
    lines = []
    for i, s in enumerate(sources):
        content = (s.content or s.snippet or "").strip()
        if len(content) > 1000:
            content = content[:1000] + "…"
        lines.append(
            f"[{offset + i}] 标题：{s.title}\n"
            f"    URL：{s.url}\n"
            f"    站点：{s.site}  时间：{s.published_at}\n"
            f"    内容：{content}"
        )
    return "\n\n".join(lines)


def _dump_evidence(sources: list[Source], evidences: list[Evidence]) -> str:
    """把来源列表 + 证据列表格式化为文本，供补检判定与综合使用。"""
    parts = ["来源列表："]
    for i, s in enumerate(sources):
        parts.append(f"[来源{i}] {s.title}（{s.url}，{s.site}，{s.published_at}）")
    parts.append("")
    parts.append("证据列表：")
    for ev in evidences:
        ref = f"[来源{ev.source_index}]" if ev.source_index is not None else "[模型推断]"
        quote = f" 原文：{ev.quote}" if ev.quote else ""
        parts.append(f"- {ev.claim}{quote} {ref}")
    if not evidences:
        parts.append("（暂无证据）")
    return "\n".join(parts)
