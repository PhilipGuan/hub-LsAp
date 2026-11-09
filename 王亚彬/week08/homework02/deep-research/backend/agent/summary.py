"""SummaryAgent：从搜索结果（+ 可选网页正文）里抽取与子问题相关的事实。"""
from __future__ import annotations

import logging

from backend.agent.base import BaseAgent
from backend.models import SearchResult
from backend.tools import fetch_page

logger = logging.getLogger(__name__)


class SummaryAgent(BaseAgent):
    name = "summary"
    system_prompt = """你是资料阅读助手。给你一个子问题和若干搜索结果（含网页正文摘录），你要从中抽取与子问题相关的关键事实。
要求：
1. 只抽取结果里真实出现的信息，禁止编造、禁止推测。
2. 每条事实一句话，尽量带数字、时间、主体等具体信息。
3. 最多 6 条，按重要性排序；若结果里没有有用信息，返回空数组。
4. 每条事实末尾用 [n] 标注它来自第几条结果（n 为结果编号）。
5. 严格只输出 JSON，不要输出任何解释文字、不要使用 Markdown 代码块。"""
    template = "子问题：{question}\n\n搜索结果：\n{results}"
    temperature = 0.2

    def _format_results(self, results: list[SearchResult], fetch_full: bool, max_fetch: int) -> str:
        lines = []
        for i, r in enumerate(results, 1):
            body = r.text or r.snippet
            lines.append(f"[{i}] 标题：{r.title}\n    来源：{r.site_name}\n    链接：{r.url}\n    摘要：{body}")
        if fetch_full and max_fetch > 0:
            for r in results[:max_fetch]:
                body = fetch_page(r.url)
                if body:
                    lines.append(f"\n【{r.title} 页面正文摘录】\n{body[:4000]}")
        return "\n\n".join(lines)[:16000]

    def run(
        self,
        question: str,
        results: list[SearchResult],
        fetch_full: bool = False,
        max_fetch: int = 0,
    ) -> tuple[list[str], list[str]]:
        """返回 (facts, gaps)。失败返回 ([], [])，不中断流程。"""
        if not results:
            return [], []
        text = self._format_results(results, fetch_full=fetch_full, max_fetch=max_fetch)
        try:
            data = self.call(question=question, results=text)
            facts = data.get("facts") if isinstance(data, dict) else []
            gaps = data.get("gaps") if isinstance(data, dict) else []
            facts = [str(f).strip() for f in (facts or []) if str(f).strip()][:6]
            gaps = [str(g).strip() for g in (gaps or []) if str(g).strip()][:3]
            logger.info("[summary] 「%s」抽到 %s 条事实", question[:20], len(facts))
            return facts, gaps
        except Exception as exc:  # noqa: BLE001
            logger.warning("[summary] 阅读抽取失败（%s）：%s", question[:20], exc)
            return [], []


if __name__ == "__main__":
    from backend.config import setup_logging

    setup_logging()
    agent = SummaryAgent()
    demo = [
        SearchResult(
            title="2025年中国新能源汽车销量排行",
            url="https://example.com/a",
            summary="2025年比亚迪销量约420万辆，同比增长18%，市场份额约32%。",
            site_name="示例网",
        ),
        SearchResult(
            title="新势力交付量",
            url="https://example.com/b",
            summary="理想汽车2025年交付50万辆，蔚来交付22万辆，小鹏交付35万辆。",
            site_name="示例网",
        ),
    ]
    facts, gaps = agent.run("2025年各新能源车企销量如何？", demo)
    print("事实：")
    for f in facts:
        print("  -", f)
    print("缺口：")
    for g in gaps:
        print("  -", g)
