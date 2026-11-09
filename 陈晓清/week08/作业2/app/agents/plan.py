"""PlanAgent：研究主题 → 2~5 个可直接检索的子问题。

去重由**程序**负责（见 CLAUDE.md 决策 6）：规划是单次调用，规则化去重便宜且可验收；
而补检轮的去重需要语义理解，那一半交给 `JudgeAgent` 的提示词。两者不矛盾。
"""

import unicodedata

from app.agents.base import BaseAgent, LLMJsonError
from app.config import MAX_SUB_QUESTIONS, MIN_SUB_QUESTIONS


def _key(text: str) -> str:
    """判重用的规范化形式：全半角统一 → 转小写 → 去掉所有空白。

    中英文混排时「ＡＩ Agent」和「aiagent」应当算同一条，所以两侧都先过这个函数。
    """
    return "".join(unicodedata.normalize("NFKC", text).lower().split())


def _dedupe(questions: list[str]) -> list[str]:
    """规范化去重 + 包含关系合并，保持模型给的原始顺序。

    包含关系取**信息更全的那条**：若某条被另一条整条包含，丢掉它。
    等长的包含只可能是完全相等，由下面的字面判重接住。
    """
    keys = [_key(q) for q in questions]
    kept: list[str] = []
    kept_keys: list[str] = []
    for i, question in enumerate(questions):
        if any(len(keys[j]) > len(keys[i]) and keys[i] in keys[j] for j in range(len(keys))):
            continue
        if keys[i] in kept_keys:
            continue
        kept.append(question)
        kept_keys.append(keys[i])
    return kept


def _as_list(data: object) -> list[str]:
    """模型输出的收口：偶尔会把数组包成 `{"sub_questions": [...]}`，也会混进非字符串元素。"""
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), data)
    if not isinstance(data, list):
        raise LLMJsonError(f"规划结果不是数组: {data!r}")
    return [item for item in data if isinstance(item, str)]


def _normalize(raw: list[str], topic: str) -> list[str]:
    """程序侧收口：去空白 → 去重 → 包含合并 → 截断到 5 → 少于 2 条则回退为「原主题单问题」。"""
    kept = _dedupe([q.strip() for q in raw if q.strip()])[:MAX_SUB_QUESTIONS]
    return kept if len(kept) >= MIN_SUB_QUESTIONS else [topic]


class PlanAgent(BaseAgent):
    """输入主题，输出子问题列表。"""

    name = "plan"
    system_template = "plan_system.tmpl"

    async def _execute(self, topic: str) -> list[str]:
        """`run(topic=...)` 调用。失败让异常冒到编排器——Agent 不该知道怎么降级。"""
        text = await self.call_llm(self.render("plan_user.tmpl", topic=topic))
        return _normalize(_as_list(self.parse_json(text)), topic)


if __name__ == "__main__":
    # 测试 demo：真实调用 LLM（需要 .env 里的 LLM_API_KEY 与网络）
    import asyncio
    import logging
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    def _check_normalize() -> None:
        """无网络的纯本地逻辑用假数据自检：模型给什么是它的事，收口规则必须自己站得住。"""
        # 全半角 / 大小写 / 空格差异算同一条
        assert _dedupe(["AI Agent", "ＡＩ　agent", "  ai  agent  "]) == ["AI Agent"]
        # 包含关系合并：短的被长的整条包含 → 留信息更全的，且保持原顺序
        assert _dedupe(["框架对比", "2026 年主流框架对比评测", "部署成本"]) == ["2026 年主流框架对比评测", "部署成本"]
        # 超 5 截断
        assert len(_normalize([f"问题{i}" for i in range(8)], "主题")) == 5
        # 收口后不足 2 条 → 回退为原主题单问题（含「一条都没有」这种最坏情况）
        assert _normalize(["只有一条"], "原主题") == ["原主题"]
        assert _normalize([], "原主题") == ["原主题"]
        # 模型把数组包了一层 / 混进非字符串元素
        assert _as_list({"sub_questions": ["a", 1, None, "b"]}) == ["a", "b"]
        # 压根不是数组 → 明确抛错，让编排器按需求文档 6.3 走降级
        try:
            _as_list("不是数组")
        except LLMJsonError:
            pass
        else:
            raise AssertionError("非数组应抛 LLMJsonError")

        print("plan.py 本地自检 ok：判重 / 包含合并 / 截断 / 回退 / 输出收口")

    async def _demo() -> None:
        result = await PlanAgent().run(topic="年轻人为什么爱熬夜")
        for i, question in enumerate(result.value or [], 1):
            print(f"  {i}. {question}")
        print("step:", result.step)

    _check_normalize()  # 先跑本地，网络挂了也不至于连收口规则都没验
    asyncio.run(_demo())
