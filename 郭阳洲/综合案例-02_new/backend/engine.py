# -*- coding: utf-8 -*-
"""研究引擎 DeepResearch：研究循环的**编排器**（不是 agent）。

规格见 README.md / 任务说明书.md（T7）。
- 引擎自身不发起任何 LLM 调用，只做确定性控制流：
  规划（KeywordAgent）→ 逐关键词检索（直接调 tools.web_search）
  → 总结成正文段落累积进草稿（SummaryAgent）→ 判断补检（JudgeAgent，不足则进下一轮）
  → 收集来源 + 确定性计算置信度 → 综合出报告与 HTML（ReportAgent）。
- 每完成一轮通过 on_progress 回调把中间结果交给调用方（写盘），支持同步 / 异步回调。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime
from typing import Any, Callable

from . import config, tools
from .agent import JudgeAgent, KeywordAgent, ReportAgent, SummaryAgent
from .models import (
    ConfidenceNote,
    DeepResearchResult,
    DraftBlock,
    ProcessStep,
    ResearchProcess,
    Source,
)

logger = logging.getLogger(__name__)


class DeepResearch:
    """一次深度研究的完整编排。"""

    def __init__(self, topic: str):
        self.topic = topic
        self.keyword_agent = KeywordAgent()
        self.summary_agent = SummaryAgent()
        self.judge_agent = JudgeAgent()
        self.report_agent = ReportAgent()

    async def run(
        self,
        max_rounds: int | None = None,
        on_progress: Callable[[dict[str, Any]], Any] | None = None,
    ) -> DeepResearchResult:
        """跑完整研究循环，返回四类产物。"""
        max_rounds = max_rounds or config.RESEARCH_MAX_ROUNDS
        process = ResearchProcess()
        draft: list[DraftBlock] = []
        sources: list[Source] = []
        seen_urls: set[str] = set()
        all_dates: list[str] = []

        # ---------------- ① 规划：拆解子问题 ----------------
        raw_keywords = await self.keyword_agent.generate_keywords(self.topic)
        initial = [k.strip() for k in (raw_keywords or []) if k and k.strip()]
        if not initial:
            logger.warning("KeywordAgent 未产出关键词，回退为直接用主题检索")
            initial = [self.topic]
        process.plan = initial
        process.steps.append(
            ProcessStep(type="plan", round=0, detail={"keywords": initial})
        )
        logger.info("规划完成，初始关键词 %d 个: %s", len(initial), initial)
        await self._progress(on_progress, process, draft, sources)

        # ---------------- ② 多轮检索 → 总结 → 判断补检 ----------------
        todo = initial
        searched: list[str] = []
        round_no = 0
        while round_no < max_rounds:
            round_no += 1
            process.iterations = round_no
            logger.info(
                "第 %d/%d 轮检索开始，待检关键词 %d 个: %s",
                round_no,
                max_rounds,
                len(todo),
                todo,
            )

            for kw in todo:
                if kw in searched:
                    continue
                searched.append(kw)
                process.search_queries.append(kw)

                try:
                    results = await tools.web_search(kw)
                except Exception:  # noqa: BLE001 - 单次检索失败不中断整个研究
                    logger.warning("关键词 %r 检索失败，按 0 条结果继续", kw, exc_info=True)
                    results = []

                _collect_sources(results, seen_urls, sources, process, all_dates)
                process.steps.append(
                    ProcessStep(
                        type="search",
                        round=round_no,
                        detail={"keyword": kw, "results": len(results)},
                    )
                )
                logger.info(
                    "检索 关键词=%r 结果=%d 条，累计来源=%d 条",
                    kw,
                    len(results),
                    len(sources),
                )

                text = (
                    await self.summary_agent.summarize(self.topic, kw, results) or ""
                ).strip()
                draft.append(DraftBlock(round=round_no, keyword=kw, text=text))
                process.steps.append(
                    ProcessStep(
                        type="summarize",
                        round=round_no,
                        detail={"keyword": kw, "chars": len(text)},
                    )
                )
                logger.info(
                    "总结 关键词=%r 正文=%d 字，累计正文=%d 段", kw, len(text), len(draft)
                )

            # 判断是否需要补检
            decision = await self.judge_agent.judge(
                self.topic, _join_draft(draft), sources, searched
            )
            process.steps.append(
                ProcessStep(
                    type="judge",
                    round=round_no,
                    detail={
                        "sufficient": decision.sufficient,
                        "reason": decision.reason,
                        "new_keywords": list(decision.new_keywords or []),
                    },
                )
            )
            logger.info(
                "第 %d 轮判断: sufficient=%s reason=%r 新关键词=%s",
                round_no,
                decision.sufficient,
                decision.reason,
                decision.new_keywords,
            )

            await self._progress(on_progress, process, draft, sources)

            if decision.sufficient or round_no >= max_rounds:
                break

            todo = [k for k in (decision.new_keywords or []) if k not in searched]
            if not todo:
                logger.info("无新的可检索关键词，提前结束循环")
                break

        # ---------------- ③ 综合：置信度 + 报告 ----------------
        confidence = _compute_confidence(sources, draft, all_dates)
        report, report_html = await self.report_agent.generate(
            self.topic, draft, sources, confidence
        )
        logger.info(
            "研究完成: 轮数=%d 正文=%d 段 来源=%d 条 置信度=%s",
            process.iterations,
            len(draft),
            len(sources),
            confidence.overall,
        )
        return DeepResearchResult(
            report=report,
            report_html=report_html,
            sources=sources,
            draft=draft,
            process=process,
            confidence=confidence,
        )

    @staticmethod
    async def _progress(
        on_progress: Callable[[dict[str, Any]], Any] | None,
        process: ResearchProcess,
        draft: list[DraftBlock],
        sources: list[Source],
    ) -> None:
        """把当前中间结果交给回调（支持同步 / 异步）；无回调则直接返回。"""
        if on_progress is None:
            return
        result = on_progress(
            {
                "process": process.model_dump(),
                "draft": [b.model_dump() for b in draft],
                "sources": [s.model_dump() for s in sources],
            }
        )
        if inspect.isawaitable(result):
            await result


# ---------------------------------------------------------------- 模块级辅助
def _collect_sources(
    results: list[dict],
    seen_urls: set[str],
    sources: list[Source],
    process: ResearchProcess,
    all_dates: list[str],
) -> None:
    """把搜索结果按 url 去重后收集进 sources，并同步记录已读 URL 与来源日期。"""
    for item in results or []:
        url = (item.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append(
            Source(
                url=url,
                title=item.get("title") or "",
                site_name=item.get("site_name") or "",
                snippet=item.get("snippet") or "",
                accessed_at=config.today_str(),
            )
        )
        process.reviewed_urls.append(url)
        date = (item.get("date") or "").strip()
        if date:
            all_dates.append(date)


def _join_draft(draft: list[DraftBlock]) -> str:
    """把各段草稿拼成连续文字（标注来源关键词），供判断补检使用。"""
    return "\n\n".join(f"【关键词：{b.keyword}】\n{b.text}" for b in draft)


def _compute_confidence(
    sources: list[Source], draft: list[DraftBlock], all_dates: list[str]
) -> ConfidenceNote:
    """按来源数分级，并给出信息截止时间与提示（确定性计算，不调 LLM）。"""
    n_sources = len(sources)
    if n_sources >= 12:
        overall = "高"
    elif n_sources >= 5:
        overall = "中"
    else:
        overall = "低"

    info_cutoff = _latest_date(all_dates) or config.today_str()
    notes = [
        f"共 {len(draft)} 段正文，关联 {n_sources} 条来源。",
        f"信息截止时间 {info_cutoff}（取最新来源日期，可能早于当前日期）。",
        "报告中无来源支撑的结论已标注为「模型推断」，请谨慎采信。",
    ]
    return ConfidenceNote(overall=overall, info_cutoff=info_cutoff, notes=notes)


def _latest_date(dates: list[str]) -> str:
    """取日期字符串前 10 位（YYYY-MM-DD）后的最大值，无有效日期返回空串。"""
    cleaned = [d[:10] for d in dates if d]
    return max(cleaned) if cleaned else ""


# ------------------------------------------------------------------ 自检 demo
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    topic = "2026 年主流 Agent 框架对比"

    def on_progress(snapshot: dict[str, Any]) -> None:
        """同步回调：实时打印研究进度（中间结果会由 research.py 写盘）。"""
        process = snapshot["process"]
        steps = process.get("steps") or []
        last_type = steps[-1].get("type") if steps else "-"
        print(
            f"[进度] 轮次={process.get('iterations')} steps={len(steps)} "
            f"正文段={len(snapshot['draft'])} 来源={len(snapshot['sources'])} "
            f"最近一步={last_type}",
            flush=True,
        )

    engine = DeepResearch(topic)
    result = asyncio.run(engine.run(on_progress=on_progress))

    print("=" * 60)
    print(f"标题     = {result.report.title}")
    print(f"分节数   = {len(result.report.sections)}")
    print(f"关键结论 = {len(result.report.key_conclusions)} 条")
    print(f"来源数   = {len(result.sources)}")
    print(f"置信度   = {result.confidence.overall}（截至 {result.confidence.info_cutoff}）")
    print(f"HTML     = {len(result.report_html)} 字符")
    for note in result.confidence.notes:
        print(f"  - {note}")

    # 落盘到 backend/data/（不放 research/ 子目录，避免污染真实研究记录）
    out_dir = config.DATA_DIR.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"engine_demo_{stamp}.json"
    html_path = out_dir / f"engine_demo_{stamp}.html"
    json_path.write_text(
        result.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8"
    )
    html_path.write_text(result.report_html, encoding="utf-8")
    print(f"已落盘 JSON: {json_path}")
    print(f"已落盘 HTML: {html_path}")

    assert result.report.sections, "分节不应为空"
    assert result.report_html.strip(), "HTML 不应为空"
    print("研究引擎自检 OK")
