"""KeywordAgent：把研究主题拆成子问题 + 每个子问题配检索关键词。"""
from __future__ import annotations

import logging

from backend.agent.base import BaseAgent
from backend.models import SubQuestion

logger = logging.getLogger(__name__)


class KeywordAgent(BaseAgent):
    name = "keyword"
    system_prompt = """你是研究规划专家。用户给一个研究主题，你要把它拆成若干个可独立检索的子问题。
要求：
1. 子问题数量 3~6 个，彼此不重叠，合起来能覆盖主题的主要方面。
2. 每个子问题配 2~3 个中文检索关键词，关键词要具体（带年份、地区、维度），不要泛泛。
3. 关键词要能直接丢进搜索引擎。
4. 严格只输出 JSON，不要输出任何解释文字、不要使用 Markdown 代码块。"""
    template = "研究主题：{topic}\n最多拆出 {max_sub} 个子问题。"
    temperature = 0.2

    def run(self, topic: str, max_sub: int = 5) -> list[SubQuestion]:
        """返回 SubQuestion 列表；失败时退化为单子问题。"""
        try:
            data = self.call(topic=topic, max_sub=max_sub)
            raw = data.get("sub_questions") if isinstance(data, dict) else None
            if not isinstance(raw, list) or not raw:
                raise ValueError("返回结果缺少 sub_questions")
            out: list[SubQuestion] = []
            for item in raw[:max_sub]:
                if not isinstance(item, dict):
                    continue
                q = str(item.get("question", "")).strip()
                if not q:
                    continue
                kws = [str(k).strip() for k in (item.get("keywords") or []) if str(k).strip()]
                if not kws:
                    kws = [q]
                out.append(SubQuestion(question=q, keywords=kws[:3], round_added=1))
            if out:
                logger.info("[keyword] 规划出 %s 个子问题", len(out))
                return out
            raise ValueError("解析后无有效子问题")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[keyword] 规划失败，退化为单子问题：%s", exc)
            return [SubQuestion(question=topic, keywords=[topic], round_added=1)]


if __name__ == "__main__":
    from backend.config import setup_logging

    setup_logging()
    agent = KeywordAgent()
    for sq in agent.run("2026年国内新能源车企竞争格局", max_sub=4):
        print(f"- {sq.question}")
        print(f"    关键词：{' / '.join(sq.keywords)}")
