"""ReportAgent：主题 + 各分节正文及 refs + 来源清单 → 报告的其余四块。

**不重写分节正文**（CLAUDE.md 决策 8）：这里只产出摘要、关键结论、遗留问题、置信度说明，
分节正文由编排器原样透传。

也只喂分节的**正文**，不喂原始检索摘要（决策 9）——来源清单里只有编号 / 标题 / URL，
摘要内容早在 `SummaryAgent` 那一步消化完了。
"""

from app.agents.base import BaseAgent, strip_inline_refs, valid_refs
from app.models import ReportResult, Section, Source


def _format_sections(sections: list[Section]) -> str:
    """分节清单的提示词片段。带上各自的来源编号，好让结论的 refs 有据可依。"""
    if not sections:
        return "（还没有任何分节）"
    blocks = []
    for section in sections:
        refs = "".join(f"[{r}]" for r in section.refs) or "（无来源）"
        blocks.append(f"### 子问题：{section.sub_question}\n{section.text}\n本节来源：{refs}")
    return "\n\n".join(blocks)


def _format_sources(sources: list[Source]) -> str:
    """来源清单的提示词片段。**故意不带 summary**——综合阶段不碰原始摘要（决策 9）。"""
    if not sources:
        return "（本次未检索到任何来源）"
    return "\n".join(f"[{s.id}] {s.title} — {s.url}" for s in sources)


def _normalize(result: ReportResult, available_ids: set[int]) -> ReportResult:
    """收口：正文里的行内编号全删（一律由程序按 refs 拼接），refs 一律过来源表。

    摘要 / 遗留问题 / 置信度说明在需求文档 3.2 里都是「无引用」章节，所以也一并清干净。
    """
    result.summary = strip_inline_refs(result.summary)
    result.confidence_note = strip_inline_refs(result.confidence_note)
    result.open_questions = [strip_inline_refs(q) for q in result.open_questions]
    for conclusion in result.conclusions:
        conclusion.text = strip_inline_refs(conclusion.text)
        conclusion.refs = valid_refs(conclusion.refs, available_ids)
    return result


class ReportAgent(BaseAgent):
    """输入主题、各分节与来源清单，输出报告其余四块。"""

    name = "report"
    system_template = "report_system.tmpl"

    async def _execute(self, topic: str, sections: list[Section], sources: list[Source]) -> ReportResult:
        """`run(topic=..., sections=[...], sources=[...])` 调用。

        置信度**数值**不在这里算（决策 10）：本 Agent 只写定性成因，
        具体数值由 `confidence.py` 算完后，由渲染环节填进「置信度说明」。
        """
        prompt = self.render(
            "report_user.tmpl",
            topic=topic,
            sections=_format_sections(sections),
            sources=_format_sources(sources),
        )
        result = self.parse_model(await self.call_llm(prompt), ReportResult)
        return _normalize(result, {s.id for s in sources})


if __name__ == "__main__":
    # 测试 demo：真实调用 LLM（需要 .env 里的 LLM_API_KEY 与网络）
    import asyncio
    import logging
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    def _check_local() -> None:
        """无网络的纯本地逻辑用假数据自检。"""
        result = ReportResult(
            summary="摘要里不该有[1]标注",
            conclusions=[
                {"text": "结论一[1]", "refs": [1, 99, 1]},
                {"text": "纯推断的结论", "refs": []},
            ],
            open_questions=["遗留问题[2]"],
            confidence_note="成因说明[3]",
        )
        out = _normalize(result, {1, 2})
        assert out.summary == "摘要里不该有标注"
        assert out.conclusions[0].text == "结论一" and out.conclusions[0].refs == [1]  # 越界 99 已剔除
        assert out.conclusions[1].refs == []  # 空 refs 合法：那是「模型推断」
        assert out.open_questions == ["遗留问题"] and out.confidence_note == "成因说明"
        # 分节正文原样进提示词，且带上自己的来源编号
        block = _format_sections([Section(sub_question="Q", text="正文", refs=[3])])
        assert "### 子问题：Q" in block and "正文" in block and "本节来源：[3]" in block
        # 来源清单不带摘要：决策 9 的另一半
        assert "标题" in _format_sources([Source(id=1, title="标题", url="u", summary="原始摘要")])
        assert "原始摘要" not in _format_sources([Source(id=1, title="标题", url="u", summary="原始摘要")])

        print("report.py 本地自检 ok：行内编号清除 / 越界剔除 / 清单不带原始摘要")

    async def _demo() -> None:
        # 分节与来源均为演示用假数据
        sections = [
            Section(
                sub_question="2026 年国内主流 AI Agent 框架有哪些？",
                text="（演示用假数据）公开资料提到 LangChain、AutoGen、Dify、Coze 等框架。",
                refs=[1, 2],
            ),
            Section(sub_question="各框架的私有化部署成本如何？", text="公开资料未覆盖。", refs=[]),
        ]
        sources = [
            Source(id=1, title="2026 国内 AI Agent 框架横向评测", url="https://example.com/bench-2026", summary="原始摘要不该进提示词"),
            Source(id=2, title="企业级 Agent 框架选型的五个维度", url="https://example.com/selection", summary="同上"),
        ]
        result = await ReportAgent().run(
            topic="年轻人为什么爱熬夜", sections=sections, sources=sources
        )
        print("摘要:", result.value)
        if result.value:
            print("摘要:", result.value.summary)
            print("关键结论:")
            for conclusion in result.value.conclusions:
                print(f"  - {conclusion.text} {conclusion.refs}")
            print("遗留问题:", result.value.open_questions)
            print("置信度说明:", result.value.confidence_note)
        print("step:", result.step)

    _check_local()  # 先跑本地，网络挂了也不至于连收口规则都没验
    asyncio.run(_demo())
