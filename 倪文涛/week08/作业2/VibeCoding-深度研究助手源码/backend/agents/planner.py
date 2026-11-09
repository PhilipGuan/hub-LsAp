"""PlannerAgent 规划:研究主题 → 拆解为互补的子问题。"""
from config import CONFIG

from .base import AgentError, BaseAgent


class PlannerAgent(BaseAgent):
    name = "planner"

    async def run(self, topic: str) -> list:
        await self.send("status", message="正在规划研究方案,拆解子问题…")
        system = (
            "你是研究规划专家。把研究主题拆解为互相互补、可独立检索的子问题,"
            "覆盖:概念与现状、关键数据、对比/竞争格局、趋势与风险等维度。"
        )
        user = (
            f"研究主题:{topic}\n\n"
            f"请拆成 {CONFIG.num_subquestions} 个子问题,只输出 JSON:"
            '{"sub_questions": ["…", …]}\n'
            "要求:每个子问题具体、可直接用于搜索;彼此不重复;语言与研究主题一致。"
        )
        raw = await self.call_llm(system, user, temperature=0.2, max_tokens=4096)
        subs = [str(q).strip() for q in self.parse_json(raw).get("sub_questions", []) if str(q).strip()]
        if not subs:
            raise AgentError("规划失败:未得到有效子问题")
        subs = subs[: CONFIG.num_subquestions]
        await self.send("plan", sub_questions=subs)
        return subs
