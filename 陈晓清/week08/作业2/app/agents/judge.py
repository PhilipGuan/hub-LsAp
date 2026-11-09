"""JudgeAgent：主题 + 当前全部分节正文 → 充分性判定 + 补检子问题。

只在**所有子问题都总结完毕之后**跑一次（需求文档 6.1）。喂的是各分节正文，
不是原始检索摘要——综合阶段不碰原始上下文（CLAUDE.md 决策 9）。

补检子问题的**语义**去重由提示词负责，程序侧不做字面判重（决策 5）：
换一种问法问同一件事，规则判不出来。程序只兜「条数上限」这一件可验收的事。
"""

from app.agents.base import BaseAgent
from app.config import MAX_NEW_SUB_QUESTIONS
from app.models import JudgeResult, Section


def _format_sections(sections: list[Section]) -> str:
    """分节清单的提示词片段。refs 不给——判定看的是结论是否成立，不是出处编号。"""
    if not sections:
        return "（还没有任何分节）"
    return "\n\n".join(f"### 子问题：{s.sub_question}\n{s.text}" for s in sections)


def _apply_limits(result: JudgeResult) -> JudgeResult:
    """条数收口：判定充分就清空补检清单（否则记录自相矛盾），不充分则截到上限。"""
    if result.sufficient:
        result.new_sub_questions = []
    else:
        result.new_sub_questions = result.new_sub_questions[:MAX_NEW_SUB_QUESTIONS]
    return result


class JudgeAgent(BaseAgent):
    """输入主题与全部分节，输出是否充分以及要补检什么。"""

    name = "judge"
    system_template = "judge_system.tmpl"

    async def _execute(self, topic: str, sections: list[Section]) -> JudgeResult:
        """`run(topic=..., sections=[...])` 调用。

        注意：第 3 轮跑完仍不充分时，编排器**照常调用本 Agent 但忽略判定结果**
        （CLAUDE.md 决策 3）——那份「跑满仍不充分」的记录要进 judge_history。
        """
        prompt = self.render("judge_user.tmpl", topic=topic, sections=_format_sections(sections))
        return _apply_limits(self.parse_model(await self.call_llm(prompt), JudgeResult))


if __name__ == "__main__":
    # 测试 demo：真实调用 LLM（需要 .env 里的 LLM_API_KEY 与网络）
    import asyncio
    import logging
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    def _check_local() -> None:
        """无网络的纯本地逻辑用假数据自检。"""
        # 判定充分 → 补检清单必须清空
        assert _apply_limits(JudgeResult(sufficient=True, new_sub_questions=["多余的"])).new_sub_questions == []
        # 不充分 → 条数截到上限
        many = JudgeResult(sufficient=False, new_sub_questions=[f"q{i}" for i in range(10)])
        assert len(_apply_limits(many).new_sub_questions) == MAX_NEW_SUB_QUESTIONS
        # 缺 sufficient → 过闸失败，交给编排器按 6.3 视为已充分
        try:
            JudgeAgent().parse_model('{"reasons": ["只看得到理由"]}', JudgeResult)
        except Exception as exc:
            assert type(exc).__name__ == "LLMJsonError", exc
        else:
            raise AssertionError("缺 sufficient 应抛 LLMJsonError")
        # 只给 sufficient 也成立：解释性字段有默认值，不该因漏字段整条降级
        assert JudgeAgent().parse_model('{"sufficient": true}', JudgeResult).reasons == []
        assert "还没有任何分节" in _format_sections([])

        print("judge.py 本地自检 ok：充分即清空补检 / 条数截断 / sufficient 必填 / 空分节兜底")

    async def _demo() -> None:
        # 分节为演示用假数据：第 2 节故意空洞，看判定是否如实指出不充分
        sections = [
            Section(
                sub_question="2026 年国内主流 AI Agent 框架有哪些？",
                text="（演示用假数据）公开资料提到 LangChain、AutoGen、Dify、Coze 等框架，"
                "其中 LangChain 系偏编排、Dify 与 Coze 偏低代码交付。",
                refs=[1, 2],
            ),
            Section(sub_question="各框架的私有化部署成本如何？", text="公开资料未覆盖。", refs=[]),
        ]
        result = await JudgeAgent().run(topic="年轻人为什么爱熬夜", sections=sections)
        if result.value:
            print("是否充分:", result.value.sufficient)
            print("缺失角度:", result.value.missing_angles)
            print("判定理由:", result.value.reasons)
            print("补检子问题:", result.value.new_sub_questions)
        print("step:", result.step)

    _check_local()  # 先跑本地，网络挂了也不至于连收口规则都没验
    asyncio.run(_demo())
