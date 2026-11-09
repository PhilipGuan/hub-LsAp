"""SummaryAgent：把一个关键词的搜索结果总结为一段正文字。

特例：产物是纯文字（不输出结构化 JSON），因此不走 `parse_json`——直接用
`BaseAgent._run` 拿原始输出，仅兜底去除可能的 ``` 代码块包裹。
"""
import logging
import re

from .base import BaseAgent

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json|markdown)?\s*\n(.*?)\n```$", re.S)


class SummaryAgent(BaseAgent):
    name = "summary"

    @staticmethod
    def _strip_fence(text: str) -> str:
        m = _FENCE_RE.match(text.strip())
        return m.group(1).strip() if m else text.strip()

    async def summarize(self, topic: str, keyword: str, results: list[dict]) -> str:
        text = await self._run(
            "summary_agent.jinja2",
            {"topic": topic, "keyword": keyword, "results": results},
            user_input=keyword,
        )
        text = self._strip_fence(text)
        logger.info("SummaryAgent 对 %r 总结出 %d 字正文", keyword, len(text))
        return text


if __name__ == "__main__":
    import asyncio
    import logging

    from ..config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    sample = [
        {"title": "示例：天空为什么蓝", "url": "https://a.example/", "summary": "瑞利散射使短波更易被散射，天空呈蓝色。", "site_name": "例站", "date": "2026-09-01"},
    ]

    async def _demo():
        print("=== SummaryAgent 单次 LLM demo ===")
        text = await SummaryAgent().summarize("天空为什么是蓝色的", "蓝色成因", sample)
        print("正文：\n", text)

    if not settings.deepseek_api_key:
        print("未配置 DEEPSEEK_API_KEY（.env），跳过真实 LLM demo。")
    else:
        asyncio.run(_demo())