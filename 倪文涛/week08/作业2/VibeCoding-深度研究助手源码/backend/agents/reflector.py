"""ReflectorAgent 补检判断:评估信息缺口 → 生成补充查询 / 判定收敛。"""
from config import CONFIG

from .base import BaseAgent, sources_digest


class ReflectorAgent(BaseAgent):
    name = "reflector"

    async def run(self, topic: str, sub_questions: list, sources: list, query_history: list) -> dict:
        """返回 {"sufficient": bool, "reason": str, "new_queries": [str]}。"""
        await self.send("status", message="正在评估信息缺口,判断是否需要补检…")
        subs = "\n".join(f"{i}. {q}" for i, q in enumerate(sub_questions, 1))
        digest = sources_digest(sources, cap=200) or "(尚未收集到任何来源)"
        system = "你是研究质量审查员,负责判断信息是否足以完成研究报告,并找出信息缺口。"
        user = (
            f"研究主题:{topic}\n\n"
            f"子问题:\n{subs}\n\n"
            f"已执行的检索词:{'; '.join(query_history)}\n\n"
            f"已收集的来源要点:\n{digest}\n\n"
            "## 任务\n"
            "1. 逐个判断子问题是否已有充分信息(有事实、有数据,多来源更佳)。\n"
            "2. 全部充分 → sufficient=true。\n"
            "3. 否则给出补充检索词(换角度、更具体、补数据),"
            f"不超过 {CONFIG.max_queries_per_round} 条,不得与已执行的检索词重复。\n\n"
            '只输出 JSON:{"sufficient": true/false, "reason": "简短中文说明", '
            '"gaps": ["缺口…"], "new_queries": ["…"]}'
        )
        raw = await self.call_llm(system, user, temperature=0.2, max_tokens=8192)
        verdict = self.parse_json(raw)
        result = {
            "sufficient": bool(verdict.get("sufficient")),
            "reason": str(verdict.get("reason", "")),
            "new_queries": [str(q).strip() for q in verdict.get("new_queries", []) if str(q).strip()],
        }
        await self.send("reflect", **result)
        return result
