"""JudgeAgent：三个判断职责。

1. 搜索结果与子问题的相关性（relavance）
2. 已收集事实是否充分、是否需要补检（sufficiency）
3. 生成的报告是否有质量（report_quality）
"""
from __future__ import annotations

import logging

from backend.agent.base import BaseAgent

logger = logging.getLogger(__name__)


class JudgeAgent(BaseAgent):
    name = "judge"

    SYSTEM_RELEVANCE = """你是相关性判断助手。判断给定的「搜索结果」是否与「子问题」相关、是否能回答它。
要求：
1. 如果至少 1 条结果与子问题直接相关，视为相关。
2. 给出简明理由（不超过 30 字）。
3. 严格只输出 JSON，不要输出任何解释文字、不要使用 Markdown 代码块。"""

    SYSTEM_SUFFICIENCY = """你是研究质量判断助手。判断「已收集事实」是否足以回答「子问题」。
**严格 JSON 输出**，必须包含：
- "sufficient"：布尔值
- "reason"：判断理由，字符串，不超过 30 字
- "followups"：数组，当 sufficient=false 时给 1~2 个补检关键词（更具体、能补上缺口）；sufficient=true 时给空数组 []

判断标准：
1. sufficient = true 当且仅当：已有事实覆盖子问题的主要方面（主体、关键数据、关键结论）
2. sufficient = false 时必须给出 followups
3. 严格只输出 JSON，不要输出任何解释文字、不要使用 Markdown 代码块。"""

    SYSTEM_REPORT = """你是报告质量评审。判断给你的「研究报告」在以下维度上是否合格：
- 事实可追溯：关键结论是否有 [n] 引用且对应来源
- 覆盖完整：主题的主要方面是否都覆盖
- 表述清晰：是否清晰、无明显重复
要求：
1. 评分 0~100，>= 80 视为合格。
2. 如果不合格，给出最多 3 条具体问题与改进建议。
3. 严格只输出 JSON，不要输出任何解释文字、不要使用 Markdown 代码块。"""

    def judge_relevance(self, question: str, snippet: str) -> tuple[bool, str]:
        """快速判断搜索摘要是否相关。失败默认 True，避免误杀。"""
        try:
            data = self._call_with(
                self.SYSTEM_RELEVANCE,
                f"子问题：{question}\n\n搜索结果摘要：\n{snippet[:1500]}",
            )
            return bool(data.get("relevant", True)), str(data.get("reason", "")).strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[judge] relevance 失败：%s", exc)
            return True, "判断失败，默认通过"

    def judge_sufficiency(
        self,
        question: str,
        facts: list[str],
        gaps: list[str],
    ) -> tuple[bool, str, list[str]]:
        """判断已收集事实是否充分。返回 (sufficient, reason, followups)。"""
        joined_facts = "\n".join(f"- {f}" for f in facts) or "（暂无事实）"
        joined_gaps = "\n".join(f"- {g}" for g in gaps) or "（暂无缺口）"
        try:
            data = self._call_with(
                self.SYSTEM_SUFFICIENCY,
                f"子问题：{question}\n\n已收集事实：\n{joined_facts}\n\n待补缺口：\n{joined_gaps}",
            )
            sufficient = bool(data.get("sufficient", False))
            reason = str(data.get("reason", "")).strip() or ("已充分" if sufficient else "需补检")
            followups = [str(k).strip() for k in (data.get("followups") or []) if str(k).strip()][:2]
            return sufficient, reason, followups
        except Exception as exc:  # noqa: BLE001
            logger.warning("[judge] sufficiency 失败：%s", exc)
            return False, "判断失败，按需补检处理", [question]

    def judge_report(self, report_markdown: str) -> tuple[int, list[str], list[str]]:
        """评审报告质量。返回 (score, issues, suggestions)。"""
        try:
            data = self._call_with(
                self.SYSTEM_REPORT,
                f"研究报告：\n```\n{report_markdown[:6000]}\n```",
            )
            score = int(data.get("score", 0))
            issues = [str(x).strip() for x in (data.get("issues") or []) if str(x).strip()][:5]
            suggestions = [str(x).strip() for x in (data.get("suggestions") or []) if str(x).strip()][:5]
            return score, issues, suggestions
        except Exception as exc:  # noqa: BLE001
            logger.warning("[judge] report 失败：%s", exc)
            return 100, [], []

    def _call_with(self, system_prompt: str, user_prompt: str) -> dict:
        """用临时 system + 临时模板走一次 LLM。"""
        from backend.llm import chat_json

        return chat_json(system_prompt, user_prompt, temperature=0.1, max_tokens=2000)

    def run(self, **kwargs) -> dict:  # type: ignore[override]
        """不直接走 run，请调用具体方法（judge_relevance/judge_sufficiency/judge_report）。"""
        raise NotImplementedError("JudgeAgent 请调用具体方法")


if __name__ == "__main__":
    from backend.config import setup_logging

    setup_logging()
    j = JudgeAgent()
    rel, reason = j.judge_relevance("比亚迪 2025 销量", "比亚迪2025年销量超348万辆，市场份额27.2%")
    print("relevance:", rel, "|", reason)
    suf, reason, fu = j.judge_sufficiency("比亚迪 2025 销量", ["比亚迪 2025 销量 348 万辆 [1]"], [])
    print("sufficiency:", suf, "|", reason, "| followups:", fu)
    sample = "# 测试\n## 摘要\n比亚迪第一。"
    score, issues, sugg = j.judge_report(sample)
    print("report score:", score, "| issues:", issues, "| suggestions:", sugg)
