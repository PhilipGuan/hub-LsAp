"""研究循环编排器(确定性循环,流程控制不交给 LLM)。

核心流程:规划 → [检索 → 阅读抽取 → 补检判断]×N(≤MAX_ROUNDS,可提前收敛) → 综合生成报告。
"""
import asyncio

from agents import (
    AgentError, PlannerAgent, ReaderAgent, ReflectorAgent, SearcherAgent,
    Source, WriterAgent,
)
from config import CONFIG


async def run_research(topic: str, emit) -> dict:
    """驱动五个智能体完成研究,返回报告与统计。"""
    if not CONFIG.bocha_api_key:
        raise AgentError("BOCHA_API_KEY 未配置,请检查项目 .env")

    sources: dict = {}          # url -> Source,引用编号按发现顺序递增
    query_history: list = []
    stats = {"rounds": 0, "searches": 0, "pages_fetched": 0, "fallbacks": 0, "skipped": 0}

    planner, searcher = PlannerAgent(emit), SearcherAgent(emit)
    reader, reflector, writer = ReaderAgent(emit), ReflectorAgent(emit), WriterAgent(emit)

    sub_questions = await planner.run(topic)
    queries: list = []

    for round_no in range(1, CONFIG.max_rounds + 1):
        stats["rounds"] = round_no
        await emit({"type": "round", "round": round_no, "max": CONFIG.max_rounds})

        if round_no == 1:
            queries = await searcher.make_queries(sub_questions)
        # 第 2、3 轮的 queries 来自上一轮 Reflector;先截断再搜索
        cap = CONFIG.max_queries_per_round if round_no > 1 else CONFIG.num_subquestions
        queries = [q for q in queries if q][:cap]
        query_history.extend(queries)
        await emit({"type": "queries", "round": round_no, "queries": queries})

        # —— 并发检索(单次失败已在 agent 内重试 1 次,最终失败跳过并注明)——
        _, results = await searcher.run(sub_questions, queries=queries)
        per_search_new: list = []
        for q, res in zip(queries, results):
            if isinstance(res, Exception):
                stats["skipped"] += 1
                await emit({"type": "skip", "stage": "search",
                            "detail": f"搜索失败已跳过:「{q}」({res})"})
                per_search_new.append([])
                continue
            stats["searches"] += 1
            new_here = []
            for it in res:
                if it["url"] in sources:
                    continue
                src = Source(sid=len(sources) + 1, **it)
                sources[it["url"]] = src
                new_here.append(src)
            per_search_new.append(new_here)
            await emit({"type": "search_result", "query": q, "count": len(res),
                        "new": len(new_here),
                        "titles": [s.title for s in new_here[:3]]})

        # —— 组装本轮抓取清单:每次搜索前 top_k、全局去重、每轮 ≤ max_pages_per_round ——
        fetch_list, seen = [], set()
        for new_here in per_search_new:
            for s in new_here[: CONFIG.fetch_top_k]:
                if s.url not in seen:
                    seen.add(s.url)
                    fetch_list.append(s)
        fetch_list = fetch_list[: CONFIG.max_pages_per_round]
        if fetch_list:
            await reader.run(fetch_list, topic)   # 并发抓正文 + 抽要点(失败降级 summary)
            stats["pages_fetched"] += sum(s.fetched for s in fetch_list)
            stats["fallbacks"] += sum(not s.fetched for s in fetch_list)

        if round_no >= CONFIG.max_rounds:
            break
        # —— 补检判断:信息充分提前收敛,否则带新查询进入下一轮 ——
        verdict = await reflector.run(topic, sub_questions, list(sources.values()), query_history)
        if verdict["sufficient"]:
            await emit({"type": "converge", "reason": verdict["reason"]})
            break
        queries = [q for q in verdict["new_queries"] if q not in query_history]
        if not queries:
            await emit({"type": "skip", "stage": "reflect",
                        "detail": "未给出有效补充查询,提前结束检索"})
            break

    md = await writer.run(topic, sub_questions, list(sources.values()))
    html = writer.build_html(md, topic, stats)
    return {
        "markdown": md,
        "html": html,
        "stats": stats,
        "sources": [
            {"sid": s.sid, "title": s.title, "url": s.url, "domain": s.domain,
             "fetched": s.fetched} for s in sources.values()
        ],
    }
