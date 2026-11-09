"""SummaryAgent：单个子问题 + 它的全部来源 → 报告的一个分节。

按子问题分批，每次调用只喂「一个问题 + 它的全部来源」，每条来源摘要截断到
`SOURCE_SUMMARY_MAX_CHARS`（需求文档 5.4）——这是唯一会随来源数量膨胀的输入。
"""

from app.agents.base import BaseAgent, LLMJsonError, strip_inline_refs, valid_refs
from app.config import SOURCE_SUMMARY_MAX_CHARS
from app.models import Section, Source, SummaryResult


def _format_sources(sources: list[Source]) -> str:
    """来源清单的提示词片段。"""
    if not sources:
        return "（本次未检索到任何来源）"
    return "\n\n".join(
        f"[{s.id}] {s.title}\nURL: {s.url}\n摘要: {s.summary[:SOURCE_SUMMARY_MAX_CHARS]}" for s in sources
    )


class SummaryAgent(BaseAgent):
    """输入一个子问题及其全部来源，输出该分节的正文与引用编号。"""

    name = "summary"
    system_template = "summary_system.tmpl"

    async def _execute(self, sub_question: str, sources: list[Source]) -> Section:
        """`run(sub_question=..., sources=[...])` 调用。"""
        prompt = self.render(
            "summary_user.tmpl", sub_question=sub_question, sources=_format_sources(sources)
        )
        result = self.parse_model(await self.call_llm(prompt), SummaryResult)
        return Section(
            sub_question=sub_question,
            text=strip_inline_refs(result.section_text),
            refs=valid_refs(result.refs, {s.id for s in sources}),
        )


if __name__ == "__main__":
    # 测试 demo：真实调用 LLM（需要 .env 里的 LLM_API_KEY 与网络）
    import asyncio
    import logging
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    def _check_local() -> None:
        """无网络的纯本地逻辑用假数据自检：模型给什么是它的事，程序侧的收口必须自己站得住。"""
        # 越界编号剔除 + 去重 + 升序
        assert valid_refs([5, 2, 99, 5], {2, 5}) == [2, 5]
        assert valid_refs([], {2, 5}) == []
        # 行内引用标注（半角 / 全角）一律删除
        assert strip_inline_refs("结论[1]。补充【2】。") == "结论。补充。"
        # 每条来源摘要截断到 SOURCE_SUMMARY_MAX_CHARS
        block = _format_sources([Source(id=1, title="t", url="u", summary="字" * 900)])
        assert "字" * SOURCE_SUMMARY_MAX_CHARS in block and "字" * (SOURCE_SUMMARY_MAX_CHARS + 1) not in block
        assert "未检索到" in _format_sources([])
        # parse_model 把 ValidationError 折成 LLMJsonError，降级表才不会多出第三种错
        for bad in ('{"refs": [1]}', '{"section_text": ""}'):
            try:
                SummaryAgent().parse_model(bad, SummaryResult)
            except LLMJsonError:
                pass
            else:
                raise AssertionError(f"应抛 LLMJsonError: {bad}")
        assert SummaryAgent().parse_model('{"section_text": "x"}', SummaryResult).refs == []

        print("summary.py 本地自检 ok：越界剔除 / 行内引用剥离 / 截断 / 校验折错")

    async def _demo() -> None:
        # search tool 还没落地，这里造 3 条来源（假数据）：编号故意不连续，且第 3 条完全跑题，
        # 用来看编号是否原样透传、跑题那条会不会被 refs 排除
        sources = [
            Source(
                id=1,
                title="2026 国内 AI Agent 框架横向评测",
                url="https://example.com/bench-2026",
                summary="（演示用假数据）本次评测覆盖 12 个国产框架：编排灵活性上 LangChain 系领先；"
                "Dify 在低代码场景部署最快，平均响应延迟约 800ms；私有化部署成本最低的是 AutoGen 系。",
            ),
            Source(
                id=2,
                title="企业级 Agent 框架选型的五个维度",
                url="https://example.com/selection",
                summary="（演示用假数据）选型应重点看编排能力、工具生态、模型兼容性、私有化部署成本、"
                "可观测性五点，其中模型兼容性决定后续能否平滑切换到新模型。",
            ),
            Source(
                id=7,
                title="家常红烧肉的做法",
                url="https://example.com/recipe",
                summary="（演示用假数据）五花肉切块冷水下锅焯水，加冰糖炒糖色，倒入生抽老抽料酒，小火炖 40 分钟。",
            ),
        ]
        result = await SummaryAgent().run(
            sub_question="年轻人为什么爱熬夜", sources=sources
        )
        print("分节正文:", result.value.text if result.value else None)
        print(
            "引用编号:",
            result.value.refs if result.value else None,
            f"（喂进去的编号: {[s.id for s in sources]}）",
        )
        print("step:", result.step)

    _check_local()  # 先跑本地，网络挂了也不至于连收口规则都没验
    asyncio.run(_demo())
