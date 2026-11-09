"""KeywordAgent：把研究主题拆解为搜索关键词（结构化 JSON，走 parse_json）。"""
import logging

from ..models import KeywordOutput
from .base import BaseAgent

logger = logging.getLogger(__name__)


class KeywordAgent(BaseAgent):
    name = "keyword"

    async def generate_keywords(self, topic: str) -> list[str]:
        out: KeywordOutput = await self.call_json(
            "keyword_agent.jinja2", {}, topic, KeywordOutput
        )
        kws = [k.strip() for k in out.keywords if k and k.strip()]
        # 极限去重且保留顺序
        seen, unique = set(), []
        for k in kws:
            if k not in seen:
                seen.add(k)
                unique.append(k)
        logger.info("KeywordAgent 生成 %d 个关键词: %s", len(unique), unique)
        return unique


if __name__ == "__main__":
    import asyncio
    import logging

    from ..config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _demo():
        print("=== KeywordAgent 单次 LLM demo ===")
        topic = "2026 年国产大模型的选型与趋势"
        kws = await KeywordAgent().generate_keywords(topic)
        for i, k in enumerate(kws, 1):
            print(f"[{i}] {k}")

    if not settings.deepseek_api_key:
        print("未配置 DEEPSEEK_API_KEY（.env），跳过真实 LLM demo。")
    else:
        asyncio.run(_demo())