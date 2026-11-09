# -*- coding: utf-8 -*-
"""KeywordAgent：把研究主题拆解成可检索的搜索关键词（研究循环的「规划」环节）。

无工具的**单次** LLM 调用；输出结构化 JSON，由基类 parse_json 解析。
"""

from __future__ import annotations

import logging

from .. import config
from ..models import KeywordOutput
from .base import BaseAgent

logger = logging.getLogger(__name__)


class KeywordAgent(BaseAgent):
    """生成搜索关键词。"""

    agent_name = "KeywordAgent"
    template_name = "keyword_agent.jinja2"

    async def generate_keywords(self, topic: str) -> list[str]:
        """输入研究主题，输出 3~5 个覆盖不同子角度的搜索关键词。"""
        logger.info("KeywordAgent: 开始拆解主题 %r", topic)
        out = await self.call_json(
            {"topic": topic, "today": config.today_str()},
            topic,
            KeywordOutput,
        )
        logger.info(
            "KeywordAgent: 生成 %d 个关键词 %s", len(out.keywords), out.keywords
        )
        return out.keywords


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    keywords = asyncio.run(KeywordAgent().generate_keywords("2026 年主流 Agent 框架对比"))
    print(f"生成 {len(keywords)} 个关键词：")
    for i, kw in enumerate(keywords, 1):
        print(f"  {i}. {kw}")
    assert keywords, "关键词不应为空"
    print("KeywordAgent 自检 OK")
