# -*- coding: utf-8 -*-
"""ReportAgent：产出结构化报告 + 自包含 HTML（两次 LLM 调用）。

- 第一次调 report_agent.jinja2：只补报告元信息 ReportOutline（标题 / 摘要 / 关键结论 / 遗留问题）；
- **正文分节由 draft 直接映射**（heading=keyword、body=text），不让 LLM 重写；
- 第二次调 report_html_agent.jinja2：把组装好的 ReportContent 渲染成完整 HTML 原文。
"""

from __future__ import annotations

import json
import logging
import re

from .. import config
from ..models import (
    ConfidenceNote,
    DraftBlock,
    ReportContent,
    ReportOutline,
    Section,
    Source,
)
from .base import BaseAgent

logger = logging.getLogger(__name__)

# 兜底：模型把 HTML 用 ```html 围栏包起来时剥掉
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*(.*?)\s*```$", re.S)


class ReportAgent(BaseAgent):
    """生成结构化报告与 HTML。"""

    agent_name = "ReportAgent"
    template_name = "report_agent.jinja2"

    async def generate(
        self,
        topic: str,
        draft: list[DraftBlock],
        sources: list[Source],
        confidence: ConfidenceNote,
    ) -> tuple[ReportContent, str]:
        """输入草稿 / 来源 / 置信度，输出 (结构化报告, HTML 原文)。"""
        system_vars = {"topic": topic, "today": config.today_str()}
        materials = {
            "draft": [{"keyword": b.keyword, "text": b.text} for b in draft],
            "sources": [s.model_dump() for s in sources],
            "confidence": confidence.model_dump(),
        }

        # ---- 第一次调用：报告元信息 ----
        logger.info(
            "ReportAgent: 生成元信息（草稿 %d 段，来源 %d 条）", len(draft), len(sources)
        )
        outline = await self.call_json(
            system_vars,
            json.dumps(materials, ensure_ascii=False),
            ReportOutline,
        )
        logger.info(
            "ReportAgent: 标题=%r 关键结论 %d 条 遗留问题 %d 条",
            outline.title,
            len(outline.key_conclusions),
            len(outline.open_questions),
        )

        # ---- 正文分节：由 draft 直接映射，不走 LLM ----
        report = ReportContent(
            title=outline.title,
            summary=outline.summary,
            sections=[Section(heading=b.keyword, body=b.text) for b in draft],
            key_conclusions=outline.key_conclusions,
            open_questions=outline.open_questions,
        )
        logger.info("ReportAgent: 组装报告，分节 %d 个", len(report.sections))

        # ---- 第二次调用：渲染 HTML ----
        # 用 json.dumps 传，保证中文不转义（model_dump_json 无 ensure_ascii 参数）
        report_json = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
        html = await self._run(
            system_vars, report_json, template_name="report_html_agent.jinja2"
        )
        html = (html or "").strip()
        fence = _FENCE_RE.match(html)
        if fence:
            logger.debug("ReportAgent: HTML 去掉代码块围栏")
            html = fence.group(1).strip()
        logger.info("ReportAgent: HTML 渲染完成，%d 字符", len(html))
        return report, html


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    mock_draft = [
        DraftBlock(
            round=1,
            keyword="Agent 框架 对比 2026",
            text="当前主流 Agent 框架在编排方式上差异明显：图式编排强调流程可控，"
            "SDK 式方案上手更快，多智能体协作框架则侧重角色分工。",
        ),
        DraftBlock(
            round=1,
            keyword="Agent 框架 选型 实践",
            text="选型时通常关注可控性、生态成熟度与调试体验；"
            "轻量场景倾向直接用 SDK，复杂流程偏向图式编排。",
        ),
    ]
    mock_sources = [
        Source(
            url="https://example.com/a",
            title="Agent 框架概览",
            site_name="example.com",
            snippet="主流 Agent 框架的编排能力对比。",
            accessed_at=config.today_str(),
        ),
        Source(
            url="https://example.org/b",
            title="框架选型实践",
            site_name="example.org",
            snippet="选型关注可控性、生态与调试体验。",
            accessed_at=config.today_str(),
        ),
    ]
    mock_confidence = ConfidenceNote(
        overall="中",
        info_cutoff=config.today_str(),
        notes=["来源 2 条，覆盖 2 个角度。"],
    )

    report, html = asyncio.run(
        ReportAgent().generate(
            topic="2026 年主流 Agent 框架对比",
            draft=mock_draft,
            sources=mock_sources,
            confidence=mock_confidence,
        )
    )
    print(f"标题     = {report.title}")
    print(f"分节数   = {len(report.sections)}")
    print(f"关键结论 = {len(report.key_conclusions)} 条")
    print(f"遗留问题 = {len(report.open_questions)} 条")
    print(f"HTML     = {len(html)} 字符")
    print(html[:300] + ("..." if len(html) > 300 else ""))
    assert report.sections, "分节不应为空"
    assert html.strip(), "HTML 不应为空"
    print("ReportAgent 自检 OK")
