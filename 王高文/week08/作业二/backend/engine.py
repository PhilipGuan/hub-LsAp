"""研究引擎 DeepResearch：编排器，不是 agent。

不参与任何 LLM 调用，只做确定性控制流：
规划（KeywordAgent）→ 逐关键词 `web_search`（直接函数调用）→ 总结成一段正文并直接
累积进草稿 draft（SummaryAgent）→ 基于累积草稿判断是否补检（JudgeAgent，不足则用新关键
词进下一轮，最多 `max_rounds` 轮）→ 确定性计算置信度 → 报告（ReportAgent）产出结构化
报告（元信息 + 草稿映射正文）+ HTML。逐步累积 `process.steps`、`draft` 与 `sources`，
每完成一轮通过 `on_progress` 回调把中间结果上报。
"""
import inspect
import logging
from datetime import datetime

from .agent.judge import JudgeAgent
from .agent.keyword import KeywordAgent
from .agent.report import ReportAgent
from .agent.summary import SummaryAgent
from .config import settings
from .models import (
    ConfidenceInfo,
    ConfidenceLevel,
    DraftBlock,
    ProcessStep,
    ResearchProcess,
    ResearchResult,
    Source,
)
from .tools import web_search

logger = logging.getLogger(__name__)


class DeepResearch:
    """研究引擎（编排器）：组合 agent/ 各角色的流水线。"""

    def __init__(self, max_rounds: int | None = None):
        self.max_rounds = max_rounds or settings.research_max_rounds
        self.keyword_agent = KeywordAgent()
        self.summary_agent = SummaryAgent()
        self.judge_agent = JudgeAgent()
        self.report_agent = ReportAgent()

    @staticmethod
    async def _progress(process, draft_text, sources, on_progress) -> None:
        """回调 on_progress(process, draft_text, sources)；支持同步或异步。"""
        if on_progress is None:
            return
        out = on_progress(process, draft_text, sources)
        if inspect.isawaitable(out):
            await out

    async def run(self, topic: str, on_progress=None) -> ResearchResult:
        logger.info("研究开始 topic=%r max_rounds=%d", topic, self.max_rounds)
        process = ResearchProcess(topic=topic)
        draft: list[DraftBlock] = []
        draft_text = ""
        sources: dict[str, Source] = {}

        def now_steps():
            process.reviewed_urls = list(sources.keys())

        def add_step(kind: str, detail: str) -> ProcessStep:
            step = ProcessStep(kind=kind, detail=detail)
            process.steps.append(step)
            return step

        # ---- 规划 ----
        add_step("plan", "拆解研究主题并生成初始搜索关键词")
        keywords = await self.keyword_agent.generate_keywords(topic)
        process.plan = list(keywords)
        process.search_queries = list(keywords)  # 初始关键词即第一轮检索关键词
        process.steps[-1].detail = f"生成初始关键词 {len(keywords)} 个：{keywords}"
        await self._progress(process, draft_text, list(sources.values()), on_progress)

        # ---- 多轮检索循环 ----
        for rnd in range(1, self.max_rounds + 1):
            process.iterations = rnd
            logger.info("第 %d 轮检索，本轮关键词 %d 个", rnd, len(keywords))
            round_keywords = list(keywords)
            next_keywords: list[str] = []

            for kw in round_keywords:
                # 检索
                s_step = add_step("search", f"检索: {kw}")
                results = await web_search(kw, summary=True, count=settings.bocha_default_count)
                added = 0
                for res in results:
                    url = (res.get("url") or "").strip()
                    if not url or url in sources:
                        continue
                    sources[url] = Source(
                        url=url,
                        title=(res.get("title") or ""),
                        snippet=(res.get("summary") or ""),
                        site_name=(res.get("site_name") or ""),
                        date=(res.get("date") or ""),
                    )
                    added += 1
                now_steps()
                s_step.detail = f"检索 '{kw}' 得 {len(results)} 条，新增来源 {added} 条"

                # 总结并累积进草稿
                st_step = add_step("summarize", f"总结: {kw}")
                text = await self.summary_agent.summarize(topic, kw, results)
                draft.append(DraftBlock(heading=kw, body=text))
                draft_text = (draft_text + "\n\n" + text).strip()
                st_step.detail = f"总结 '{kw}' 得 {len(text)} 字正文"
                await self._progress(process, draft_text, list(sources.values()), on_progress)

            # 判断是否补检
            j_step = add_step("judge", "判断信息是否充分")
            decision = await self.judge_agent.judge(
                topic, draft_text, list(sources.values()), process.search_queries
            )
            if decision.sufficient:
                j_step.detail = "信息充分，结束补检"
                await self._progress(process, draft_text, list(sources.values()), on_progress)
                logger.info("第 %d 轮判断为充分，提前收敛", rnd)
                break

            next_keywords = [k.strip() for k in decision.new_keywords if k and k.strip() and k not in process.search_queries]
            j_step.detail = f"信息不足需补检，新关键词 {len(next_keywords)} 个"
            for k in next_keywords:
                if k not in process.search_queries:
                    process.search_queries.append(k)
            keywords = next_keywords
            await self._progress(process, draft_text, list(sources.values()), on_progress)
            if not next_keywords:
                logger.warning("判断为不足但未给出新关键词，回到检索前打断循环")
                break
        else:
            logger.info("达到最大检索轮数 %d，强制结束", self.max_rounds)

        now_steps()

        # ---- 确定性置信度 ----
        confidence = self._compute_confidence(sources, process)

        # ---- 报告（元信息 + HTML）----
        report, html = await self.report_agent.generate(
            topic, draft, list(sources.values()), confidence
        )

        result = ResearchResult(
            report=report,
            report_html=html,
            sources=list(sources.values()),
            process=process,
            confidence=confidence,
        )
        logger.info(
            "研究完成 来源=%d 轮次=%d 分节=%d",
            len(result.sources), process.iterations, len(report.sections),
        )
        return result

    @staticmethod
    def _compute_confidence(sources: dict[str, Source], process: ResearchProcess) -> ConfidenceInfo:
        n = len(sources)
        if n >= 5:
            level = ConfidenceLevel.HIGH
        elif n >= 2:
            level = ConfidenceLevel.MEDIUM
        else:
            level = ConfidenceLevel.LOW
        return ConfidenceInfo(
            level=level,
            information_cutoff=datetime.now().strftime("%Y-%m-%d"),
            source_count=n,
            iteration_count=process.iterations,
            note=(
                "置信度按去重后来源数与检索轮次数确定性估算；关键结论与遗留问题由模型推断"
                "生成，正文分节基于所检索来源；无来源结论一律视为模型推断。"
            ),
        )


if __name__ == "__main__":
    import asyncio
    import logging

    from .config import settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    async def _sync_progress(process, draft_text, _sources):
        # 同步回调：打印当前过程
        print(f"  [process] 轮={process.iterations} steps={len(process.steps)} 草稿={len(draft_text)}字")

    async def _demo():
        print("=== DeepResearch 完整流水线（端到端） ===")
        result = await DeepResearch().run("天空为什么是蓝色的", on_progress=_sync_progress)
        print("标题:", result.report.title)
        print("正文分节数:", len(result.report.sections))
        print("来源数:", len(result.sources))
        print("置信度:", result.confidence.level.value, "来源", result.confidence.source_count)
        print("HTML 预览:", (result.report_html or "")[:120].replace("\n", " "))

    if not settings.deepseek_api_key:
        print("未配置 DEEPSEEK_API_KEY/BOCHA_API_KEY（.env），跳过端到端 demo。")
    else:
        asyncio.run(_demo())