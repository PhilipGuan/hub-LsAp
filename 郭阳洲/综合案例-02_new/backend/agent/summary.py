# -*- coding: utf-8 -*-
"""SummaryAgent：把单个关键词的搜索结果综合成一段报告正文（研究循环的「阅读抽取」环节）。

**特例：不走 JSON 解析**——它的产物就是报告正文段落（纯文字），
因此直接用 BaseAgent._run 拿原始输出，只做「去掉代码块围栏」的兜底。
"""

from __future__ import annotations

import json
import logging
import re

from .. import config
from .base import BaseAgent

logger = logging.getLogger(__name__)

# 兜底：模型顺手用 ``` 包起来时，剥掉围栏（正文不是 JSON，所以不用基类的 _extract_fence）
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*(.*?)\s*```$", re.S)


class SummaryAgent(BaseAgent):
    """把一个关键词的搜索结果总结成一段正文。"""

    agent_name = "SummaryAgent"
    template_name = "summary_agent.jinja2"

    async def summarize(self, topic: str, keyword: str, results: list[dict]) -> str:
        """输入主题 + 关键词 + 该关键词的搜索结果，输出一段报告正文。"""
        user_input = json.dumps(results, ensure_ascii=False)
        logger.info(
            "SummaryAgent: 开始总结 关键词=%r 结果=%d 条", keyword, len(results)
        )
        text = await self._run(
            {"topic": topic, "keyword": keyword, "today": config.today_str()},
            user_input,
        )
        text = (text or "").strip()
        fence = _FENCE_RE.match(text)
        if fence:
            logger.debug("SummaryAgent: 输出去掉代码块围栏")
            text = fence.group(1).strip()
        logger.info("SummaryAgent: 关键词 %r 总结出 %d 字正文", keyword, len(text))
        return text


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    mock_results = [
        {
            "title": "Agent 框架概览",
            "url": "https://example.com/a",
            "snippet": "当前主流 Agent 框架包括 LangGraph、AutoGen、OpenAI Agents SDK 等，"
            "各自在编排方式、工具调用与多智能体协作上侧重不同。",
            "site_name": "example.com",
            "date": "2026-01-15",
        },
        {
            "title": "框架选型实践",
            "url": "https://example.com/b",
            "snippet": "选型时通常关注三件事：可控性、生态成熟度与调试体验；"
            "轻量场景倾向直接用 SDK，复杂流程则偏向图式编排。",
            "site_name": "example.org",
            "date": "2026-02-02",
        },
    ]

    text = asyncio.run(
        SummaryAgent().summarize("2026 年主流 Agent 框架对比", "Agent 框架 对比 2026", mock_results)
    )
    print(f"正文长度 = {len(text)} 字")
    print(text[:200] + ("..." if len(text) > 200 else ""))
    assert text.strip(), "正文不应为空"
    print("SummaryAgent 自检 OK")
