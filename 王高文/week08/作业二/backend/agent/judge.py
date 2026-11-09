"""JudgeAgent：基于累积草稿判断是否需要补检，并给出新关键词。"""
import logging

from ..models import JudgeDecision
from .base import BaseAgent

logger = logging.getLogger(__name__)


class JudgeAgent(BaseAgent):
    name = "judge"

    async def judge(
        self,
        topic: str,
        draft_text: str,
        sources: list,
        searched: list[str],
    ) -> JudgeDecision:
        out: JudgeDecision = await self.call_json(
            "judge_agent.jinja2",
            {"draft_text": draft_text, "sources": sources, "searched": searched},
            user_input=topic,
            output_cls=JudgeDecision,
        )
        logger.info(
            "JudgeAgent 判断 sufficient=%s 新关键词=%s", out.sufficient, out.new_keywords
        )
        return out


if __name__ == "__main__":
    import asyncio
    import logging

    from ..config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _demo():
        print("=== JudgeAgent 单次 LLM demo ===")
        decision = await JudgeAgent().judge(
            "天空为什么是蓝色的",
            "草稿：瑞利散射使天空呈蓝色……",
            [{"url": "https://a.example/", "title": "A"}],
            searched=["蓝色成因"],
        )
        print("sufficient:", decision.sufficient)
        print("reason    :", decision.reason)
        print("new_kw    :", decision.new_keywords)

    if not settings.deepseek_api_key:
        print("未配置 DEEPSEEK_API_KEY（.env），跳过真实 LLM demo。")
    else:
        asyncio.run(_demo())