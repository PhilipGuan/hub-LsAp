"""报告渲染：把四个 Agent 的产出拼成最终的 `report` 视图（Markdown + 结构化）。

**「全局来源编号 → 报告内连续编号」的映射只在这里建一次。** 正文的 `[n]`、关键结论的
`[n]`、参考来源列表三处必须同号，任何一处单独编号都会串号，而串号就等于验收项 3 挂掉。
`process` 里保留全局编号不动——它记录的是"检索到了什么"，与报告的呈现编号是两回事。

纯函数，无 IO、无网络：落盘由编排器做。
"""

from app.models import Conclusion, Confidence, Report, ReportResult, Section, Source

_EMPTY = "（无）"


def _numbering(
    sections: list[Section], conclusions: list[Conclusion], sources: list[Source]
) -> dict[int, int]:
    """被引用到的全局编号 → 报告内连续编号，按全局编号升序，只收「有来源可查」的那些。

    没被任何地方引用的来源不进映射，于是也不会出现在参考来源里（决策 12）。
    """
    available = {s.id for s in sources}
    cited = {r for s in sections for r in s.refs}
    cited |= {r for c in conclusions for r in c.refs}
    return {global_id: i for i, global_id in enumerate(sorted(cited & available), start=1)}


def _refs_text(refs: list[int]) -> str:
    """`[1][2]`。空列表返回空串，由调用方决定写「无」还是「（模型推断）」。"""
    return "".join(f"[{n}]" for n in refs)


def _remap(refs: list[int], mapping: dict[int, int]) -> list[int]:
    return [mapping[r] for r in refs if r in mapping]


def _render_markdown(
    topic: str,
    report: ReportResult,
    conclusions: list[Conclusion],
    sections: list[Section],
    sources: list[Source],
    confidence: Confidence,
) -> str:
    """按需求文档 3.2 的固定模板渲染。

    六个章节**恒定齐全**（验收项 2）：内容为空也要出标题——缺章节比缺内容更难解释。
    """
    lines = [f"# 研究报告：{topic}", "", "## 摘要", report.summary, "", "## 关键结论"]
    lines += [
        f"{i}. {c.text}{_refs_text(c.refs) or '（模型推断）'}" for i, c in enumerate(conclusions, 1)
    ] or [_EMPTY]

    lines += ["", "## 分节正文"]
    for section in sections:
        # 分节正文原样透传（决策 8），只在节末补一行程序拼的来源
        lines += ["", f"### {section.sub_question}", section.text, f"本节来源：{_refs_text(section.refs) or '无'}"]

    lines += ["", "## 遗留问题"]
    lines += [f"- {q}" for q in report.open_questions] or [_EMPTY]

    lines += ["", "## 参考来源"]
    lines += [f"[{s.id}] {s.title} — {s.url}" for s in sources] or [_EMPTY]

    lines += [
        "",
        "## 置信度说明",
        f"整体置信度 {confidence.overall:.2f}（{confidence.level}）。"
        f"信息截止时间：{confidence.info_cutoff}。模型推断条目：{confidence.unsourced_claims} 条。",
        report.confidence_note,
    ]
    return "\n".join(lines)


def render_report(
    topic: str,
    report: ReportResult,
    sections: list[Section],
    sources: list[Source],
    confidence: Confidence,
) -> Report:
    """唯一的对外入口。`report` 是 ReportAgent 的四块，`sections` 是全部分节。"""
    mapping = _numbering(sections, report.conclusions, sources)

    out_conclusions = [
        Conclusion(text=c.text, refs=_remap(c.refs, mapping)) for c in report.conclusions
    ]
    out_sections = [
        Section(sub_question=s.sub_question, text=s.text, refs=_remap(s.refs, mapping)) for s in sections
    ]
    # 只留被引用过的（决策 12），并按新编号排好序
    out_sources = sorted(
        (Source(id=mapping[s.id], title=s.title, url=s.url, summary=s.summary) for s in sources if s.id in mapping),
        key=lambda s: s.id,
    )

    return Report(
        markdown=_render_markdown(topic, report, out_conclusions, out_sections, out_sources, confidence),
        sections=out_sections,
        conclusions=out_conclusions,
        sources=out_sources,
    )


if __name__ == "__main__":
    # 纯本地渲染（无网络），按约定用假数据自检并打印真实渲染结果
    import json
    import sys
    from pathlib import Path

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码

    # 演示数据：分节/来源取自前面四个 Agent 真跑出来的形状，全局编号故意留空档（2、7 被引用）
    sections = [
        Section(sub_question="2026 年国内主流 AI Agent 框架有哪些？", text="公开资料提到 LangChain、AutoGen、Dify、Coze。", refs=[2, 7]),
        Section(sub_question="各框架的私有化部署成本如何？", text="公开资料未覆盖。", refs=[]),
    ]
    all_sources = [
        Source(id=1, title="未被引用的来源", url="https://example.com/unused", summary="不进参考来源"),
        Source(id=2, title="2026 国内 AI Agent 框架横向评测", url="https://example.com/bench-2026", summary="…"),
        Source(id=7, title="企业级 Agent 框架选型的五个维度", url="https://example.com/selection", summary="…"),
    ]
    report = ReportResult(
        summary="2026 年国内主流 Agent 框架包括 LangChain、AutoGen、Dify、Coze。（演示数据）",
        conclusions=[
            {"text": "LangChain、AutoGen、Dify、Coze 是 2026 年国内主流框架", "refs": [2, 7]},
            {"text": "私有化部署成本缺乏公开数据支撑", "refs": []},
        ],
        open_questions=["各框架私有化部署的硬件资源需求具体是多少？"],
        confidence_note="主流框架的识别有两个来源交叉验证；部署成本维度完全缺失，构成关键信息缺口。",
    )
    confidence = Confidence(
        overall=0.72, level="中", info_cutoff="2026-09-10", unsourced_claims=1
    )

    out = render_report("年轻人为什么爱熬夜", report, sections, all_sources, confidence)

    # 重编号：全局 [2][7] → 报告内 [1][2]，且三处同号
    assert [s.refs for s in out.sections] == [[1, 2], []], out.sections
    assert [c.refs for c in out.conclusions] == [[1, 2], []], out.conclusions
    assert [s.id for s in out.sources] == [1, 2], out.sources
    assert "https://example.com/unused" not in out.markdown, "未被引用的来源不该进参考来源"
    # 六个章节恒定齐全（验收项 2）
    for heading in ("## 摘要", "## 关键结论", "## 分节正文", "## 遗留问题", "## 参考来源", "## 置信度说明"):
        assert heading in out.markdown, heading
    # 每个分节都有来源行（验收项 4），无来源的写「无」；无 refs 的结论标「模型推断」
    assert out.markdown.count("本节来源：") == len(sections)
    assert "本节来源：无" in out.markdown and "（模型推断）" in out.markdown
    # 落盘形态：model_dump 出来必须是纯 dict/list，能直接 json.dumps
    dumped = out.model_dump(mode="json")
    json.dumps(dumped, ensure_ascii=False)
    print("render.py 本地自检 ok：重编号三处同号 / 六章节齐全 / 未引用来源不入表 / 可 JSON 化")

    # 存一份 .md 出来，好直接打开看渲染效果（不是任务产物，渲染层本身仍无 IO）
    out_path = Path("output/render_selftest.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out.markdown, encoding="utf-8")
    print(f"\n渲染结果已写入: {out_path.resolve()}")

    print("\n" + out.markdown)
    print("\n---- 落盘 JSON 里 report 的结构 ----")
    print(json.dumps({k: v for k, v in dumped.items() if k != "markdown"}, ensure_ascii=False, indent=2))
