"""ReportAgent：两次调用。

1) 生成报告元信息 `ReportOutline`（标题/摘要/关键结论/遗留问题）——结构化 JSON，
   正文分节由草稿段落直接映射（heading=keyword、body=text），不走 LLM 重新组织。
2) 把组装好的 `ReportContent` + sources + confidence 渲染成自包含 HTML——
   原始文本输出（`final_output` 即 HTML 原文，不包 JSON，避免转义出错）。
"""
import json
import logging

from ..models import ReportContent, ReportOutline, Source, ConfidenceInfo
from .base import BaseAgent

logger = logging.getLogger(__name__)


class ReportAgent(BaseAgent):
    name = "report"

    async def generate(
        self,
        topic: str,
        draft: list,
        sources: list[Source],
        confidence: ConfidenceInfo,
    ) -> tuple[ReportContent, str]:
        # 第一次调用：报告元信息
        outline: ReportOutline = await self.call_json(
            "report_agent.jinja2",
            {"draft": draft, "sources": sources, "confidence": confidence},
            user_input=topic,
            output_cls=ReportOutline,
        )
        logger.info("ReportAgent 元信息完成，标题=%r 结论=%d 条", outline.title, len(outline.key_conclusions))

        report = ReportContent(
            title=outline.title,
            summary=outline.summary,
            key_conclusions=outline.key_conclusions,
            open_questions=outline.open_questions,
            sections=[{"heading": b.heading, "body": b.body} for b in draft],
        )

        # 第二次调用：渲染自包含 HTML（原始文本输出）
        vars_ = {
            "topic": topic,
            "title": report.title,
            "summary": report.summary,
            "sections": [{"heading": s.heading, "body": s.body} for s in report.sections],
            "key_conclusions": report.key_conclusions,
            "open_questions": report.open_questions,
            "sources": [s.model_dump() for s in sources],
            "confidence": confidence.model_dump(),
        }
        html = await self._run("report_html_agent.jinja2", vars_, user_input=topic)
        html = _strip_html_fence(html)
        logger.info("ReportAgent HTML 渲染完成，length=%d", len(html))
        return report, html


def _strip_html_fence(text: str) -> str:
    """兜底去除 LLM 可能加上的 ```html ... ``` 包裹。"""
    import re

    t = text.strip()
    m = re.match(r"^```(?:html)?\s*\n(.*?)\n```$", t, re.S)
    return m.group(1) if m else t


if __name__ == "__main__":
    import asyncio
    import logging

    from ..config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _demo():
        print("=== ReportAgent 两次 LLM demo ===")
        draft = [
            {"heading": "蓝色成因", "body": "瑞利散射使短波被大气分子强烈散射。"},
        ]
        sources = [Source(url="https://a.example/", title="A", site_name="例站", date="2026-09-01")]
        conf = ConfidenceInfo(
            level="medium", information_cutoff="2026-09-09",
            source_count=1, iteration_count=1,
        )
        report, html = await ReportAgent().generate("天空为什么是蓝色的", draft, sources, conf)
        print("标题:", report.title)
        print("关键结论:", report.key_conclusions)
        print("HTML 长度:", len(html), "含 <html>?", "<html" in html or "<!DOCTYPE" in html.upper())

    if not settings.deepseek_api_key:
        print("未配置 DEEPSEEK_API_KEY（.env），跳过真实 LLM demo。")
    else:
        asyncio.run(_demo())