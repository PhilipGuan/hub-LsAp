"""ReportAgent：把已收集事实综合成结构化研究报告。"""
from __future__ import annotations

import logging

from backend.agent.base import BaseAgent
from backend.models import Confidence, ReportSection, ResearchReport, Source

logger = logging.getLogger(__name__)


class ReportAgent(BaseAgent):
    name = "report"
    system_prompt = """你是研究报告撰写专家。你要基于「子问题 + 已收集事实 + 来源列表」写一份带 [n] 引用的中文研究报告。
**严格 JSON 输出**，必须包含以下字段（缺一即视为失败）：
- "summary"：200~400 字的研究摘要，字符串类型
- "sections"：数组，3~5 节；每节必须含 "heading"（小标题字符串）、"content"（含 [n] 引用的正文字符串）、"source_ids"（本节引用的来源编号数组）
- "key_findings"：数组，3~5 条关键结论，每条字符串
- "open_questions"：数组，1~3 条遗留问题，每条字符串
- "confidence"：对象，必须含 "level"（'高'/'中'/'低'）、"reason"（说明依据）、"cutoff_note"（信息截止说明）

要求：
1. 引用：每段叙述里的关键事实必须在末尾用 [n] 标注来源编号，编号与来源列表一一对应，禁止编造。
2. 引用密度：每 100 字至少 1 个 [n] 引用。
3. 严格只输出 JSON，不要输出任何解释文字、不要使用 Markdown 代码块、不要在 JSON 外加任何字符。"""
    template = "研究主题：{topic}\n\n子问题与事实：\n{sub_facts}\n\n来源列表：\n{sources}\n\n请生成结构化报告（轮次 {rounds}，来源 {src_count} 条）。"
    temperature = 0.3
    max_tokens = 8000

    def _format_sources(self, sources: list[Source]) -> str:
        if not sources:
            return "（暂无来源）"
        lines = []
        for i, s in enumerate(sources, 1):
            title = (s.title or s.url or "").replace("\n", " ")
            site = s.site_name or "未知"
            lines.append(f"[{i}] {title} | 来源：{site} | URL：{s.url}")
        return "\n".join(lines)

    def _format_sub_facts(self, sub_facts: list[dict]) -> str:
        blocks = []
        for sf in sub_facts:
            q = sf.get("question", "")
            facts = sf.get("facts") or []
            blocks.append(f"## 子问题：{q}\n" + ("\n".join(f"- {f}" for f in facts) if facts else "（暂无事实）"))
        return "\n\n".join(blocks)

    def run(
        self,
        topic: str,
        sub_facts: list[dict],
        sources: list[Source],
        rounds: int = 0,
    ) -> ResearchReport:
        """综合成报告；LLM 失败时退化为"按子问题罗列事实"的兜底报告。"""
        sub_text = self._format_sub_facts(sub_facts)
        src_text = self._format_sources(sources)
        try:
            data = self.call(topic=topic, sub_facts=sub_text, sources=src_text, rounds=rounds, src_count=len(sources))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[report] LLM 生成失败，生成兜底报告：%s", exc)
            return self._fallback(topic, sub_facts, sources, rounds, reason=f"LLM 生成失败：{exc}")

        sections: list[ReportSection] = []
        for s in (data.get("sections") or []):
            if not isinstance(s, dict):
                continue
            heading = str(s.get("heading", "")).strip()
            content = str(s.get("content", "")).strip()
            sids = [str(x).strip() for x in (s.get("source_ids") or []) if str(x).strip()]
            if heading and content:
                sections.append(ReportSection(heading=heading, content=content, source_ids=sids))
        if not sections and sub_facts:
            return self._fallback(topic, sub_facts, sources, rounds, reason="LLM 返回结构异常")

        conf = data.get("confidence") if isinstance(data, dict) else {}
        if not isinstance(conf, dict):
            conf = {}
        confidence = Confidence(
            level=str(conf.get("level", "中")),
            reason=str(conf.get("reason", "")),
            source_count=len(sources),
            rounds_used=rounds,
            cutoff_note=str(conf.get("cutoff_note", "")),
        )
        report = ResearchReport(
            topic=topic,
            summary=str(data.get("summary", "")).strip() or "（摘要缺失）",
            sections=sections,
            key_findings=[str(k).strip() for k in (data.get("key_findings") or []) if str(k).strip()][:6],
            open_questions=[str(q).strip() for q in (data.get("open_questions") or []) if str(q).strip()][:4],
            confidence=confidence,
            sources=sources,
        )
        logger.info("[report] 生成完成：%s 节，%s 来源", len(sections), len(sources))
        return report

    def _fallback(
        self,
        topic: str,
        sub_facts: list[dict],
        sources: list[Source],
        rounds: int,
        reason: str = "",
    ) -> ResearchReport:
        sections: list[ReportSection] = []
        findings: list[str] = []
        for i, sf in enumerate(sub_facts, 1):
            q = sf.get("question", f"子问题 {i}")
            facts = sf.get("facts") or []
            body = "\n".join(f"- {f}" for f in facts) or "（暂无事实）"
            sections.append(ReportSection(heading=q, content=body, source_ids=[]))
            if facts:
                findings.append(facts[0])
        level = "中" if sources else "低"
        return ResearchReport(
            topic=topic,
            summary=f"LLM 报告生成失败（{reason}），下方按子问题罗列已收集事实。共收集 {len(sources)} 条来源、{sum(len(sf.get('facts', [])) for sf in sub_facts)} 条事实。",
            sections=sections,
            key_findings=findings[:5],
            open_questions=["因报告生成失败，建议结合原始来源与 LLM 抽取事实自行总结"],
            confidence=Confidence(level=level, reason=f"LLM 报告生成失败（{reason}），但事实抽取完整", source_count=len(sources), rounds_used=rounds),
            sources=sources,
        )


if __name__ == "__main__":
    from backend.config import setup_logging

    setup_logging()
    agent = ReportAgent()
    demo_sub = [
        {"question": "2025年比亚迪销量", "facts": ["比亚迪 2025 销量超 348 万辆 [1]"]},
        {"question": "价格战情况", "facts": ["2024 年 227 款车型降价 [2]"]},
    ]
    demo_src = [
        Source(title="2025年新能源车企销量", url="https://a", site_name="网易", snippet=""),
        Source(title="2024年降价盘点", url="https://b", site_name="汽车之家", snippet=""),
    ]
    rep = agent.run("2025年新能源车", demo_sub, demo_src, rounds=2)
    print("summary:", rep.summary[:120])
    print("sections:", len(rep.sections), "| sources:", len(rep.sources))
    print("confidence:", rep.confidence.level, "|", rep.confidence.reason)
    print("--- markdown 前 800 字 ---")
    print(rep.to_markdown()[:800])
