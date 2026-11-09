# -*- coding: utf-8 -*-
"""JudgeAgent：判断已检索到的材料是否足够，不足则给出补充关键词。

研究循环的「判断是否需要补检」环节，决定是否进入下一轮检索。
无工具的**单次** LLM 调用；输出结构化 JSON。
"""

from __future__ import annotations

import json
import logging

from .. import config
from ..models import JudgeDecision, Source
from .base import BaseAgent

logger = logging.getLogger(__name__)


class JudgeAgent(BaseAgent):
    """判断材料是否充足，并（在不足时）生成新的检索关键词。"""

    agent_name = "JudgeAgent"
    template_name = "judge_agent.jinja2"

    async def judge(
        self,
        topic: str,
        draft_text: str,
        sources: list[Source],
        searched_keywords: list[str],
    ) -> JudgeDecision:
        """输入主题 + 已累积草稿 + 来源 + 已检关键词，输出补检决策。"""
        user_input = json.dumps(
            {
                "searched_keywords": searched_keywords,
                "draft": draft_text,
                "source_count": len(sources),
            },
            ensure_ascii=False,
        )
        logger.info(
            "JudgeAgent: 开始判断（草稿 %d 字，来源 %d 条，已检 %d 个关键词）",
            len(draft_text),
            len(sources),
            len(searched_keywords),
        )
        decision = await self.call_json(
            {"topic": topic, "today": config.today_str()},
            user_input,
            JudgeDecision,
        )
        logger.info(
            "JudgeAgent: sufficient=%s reason=%r new_keywords=%s",
            decision.sufficient,
            decision.reason,
            decision.new_keywords,
        )
        return decision


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    mock_sources = [
        Source(
            url="https://example.com/a",
            title="Agent 框架概览",
            site_name="example.com",
            snippet="主流 Agent 框架的编排能力对比。",
            accessed_at=config.today_str(),
        )
    ]
    mock_draft = (
        "当前主流 Agent 框架在编排方式上差异明显：图式编排强调流程可控，"
        "SDK 式方案上手更快。选型时通常关注可控性、生态成熟度与调试体验。"
    )

    decision = asyncio.run(
        JudgeAgent().judge(
            topic="2026 年主流 Agent 框架对比",
            draft_text=mock_draft,
            sources=mock_sources,
            searched_keywords=["Agent 框架 对比 2026"],
        )
    )
    print(f"decision = {decision.model_dump()}")
    print(f"sufficient={decision.sufficient} 理由={decision.reason}")
    print(f"补充关键词={decision.new_keywords}")
    print("JudgeAgent 自检 OK")
