"""Report → Markdown。

README 要求报告包含四样东西，这个文件就是把它们排好：

1. 结构化研究报告（摘要 / 分节正文 / 关键结论 / 遗留问题）
2. 来源列表（结论 ↔ URL / 标题 / 来源）
3. 研究过程记录（检索了哪些关键词、读了哪些页面、迭代了几轮）
4. 置信度说明（可靠程度、信息截止时间、无来源结论标注）

**来源列表是「可追溯」的落点**：每条来源都标出抓取状态、正文来路、是否被引用。
读了却没被引用的来源也会列出来并标注 —— 「读过但没用上」本身是有价值的信息。
"""

from __future__ import annotations

from datetime import datetime

from .citation import INFERRED_PREFIX
from .models import ContentOrigin, FetchStatus, Report, SourceRef

_ORIGIN_LABEL = {
    ContentOrigin.page: "网页正文",
    ContentOrigin.search_summary: "搜索摘要（降级）",
    ContentOrigin.snippet: "搜索片段（降级）",
}

_STATUS_LABEL = {
    FetchStatus.ok: "抓取成功",
    FetchStatus.extract_failed: "正文提取失败",
    FetchStatus.http_error: "HTTP 错误",
    FetchStatus.timeout: "超时",
    FetchStatus.too_large: "内容过大",
    FetchStatus.blocked: "反爬黑名单",
    FetchStatus.unsupported_type: "非 HTML",
    FetchStatus.connection_error: "连接失败",
    FetchStatus.skipped: "未抓取",
}

_LEVEL_LABEL = {"high": "高", "medium": "中", "low": "低"}

_FACTOR_LABEL = {
    "source_count": "来源数量",
    "domain_diversity": "域名多样性",
    "authority": "来源权威性",
    "recency": "时效性",
    "corroboration": "交叉佐证",
    "fetch_success": "抓取成功率",
    "citation_coverage": "引用覆盖率",
}


def _fmt_date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else "未知"


def _section_sources(report: Report) -> str:
    lines: list[str] = [
        "| 编号 | 标题 | 来源 | 发布时间 | 抓取 | 正文来路 | 引用 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for source in report.sources:
        title = source.title.replace("|", "\\|") or "（无标题）"
        site = source.site_name or source.domain or "未知"
        origin = _ORIGIN_LABEL.get(source.content_origin, source.content_origin.value)
        status = _STATUS_LABEL.get(source.fetch_status, source.fetch_status.value)
        # 「读了没引用」是选择，「读出来 0 条笔记」是失败 —— 在来源表里
        # 都写「未引用」会让失败伪装成选择，这条来源到底贡献了什么就看不出来了。
        # 三种 0 条笔记还要再分：送读过没抽出来 / 根本没送去读（重复或只剩片段）。
        if source.cited:
            cited = "✅"
        elif source.note_count:
            cited = "— 已读未引"
        elif source.readable:
            cited = "⚠️ 阅读未抽出笔记（未参与任何结论）"
        else:
            cited = "— 未参与阅读（重复来源或仅剩片段）"

        lines.append(
            f"| [{source.sid}] | [{title}]({source.url}) | {site} | "
            f"{_fmt_date(source.published_at)} | {status} | {origin} | {cited} |"
        )

    return "\n".join(lines)


def _process_section(report: Report) -> str:
    process = report.process
    lines: list[str] = [
        f"- 迭代轮次：**{process.rounds}** 轮",
        f"- 检索关键词：**{process.total_queries}** 个",
        f"- 发现来源：**{process.total_sources_found}** 条",
        f"- 实际读取：**{process.total_sources_read}** 条",
        f"- 抽取笔记：**{process.total_notes}** 条",
        "",
    ]

    for entry in process.timeline:
        lines.append(f"**第 {entry.round} 轮** — 新增 {entry.newly_added} 条来源")
        if entry.queries:
            lines.append(f"- 检索词：{'、'.join(entry.queries)}")
        if entry.gaps:
            lines.append(f"- 发现的缺口：{'；'.join(entry.gaps)}")
        if entry.rationale:
            lines.append(f"- 判断：{entry.rationale}")
        lines.append(f"- 结论：{entry.decision}")
        lines.append("")

    if process.urls_read:
        lines.append("**读取过的页面**")
        lines.append("")
        lines.append("| 编号 | URL | 正文字数 | 来路 |")
        lines.append("| --- | --- | --- | --- |")
        for item in process.urls_read:
            lines.append(
                f"| {item.get('sid', '')} | {item.get('url', '')} | "
                f"{item.get('chars', 0)} | {item.get('origin', '')} |"
            )

    return "\n".join(lines)


def _confidence_section(report: Report) -> str:
    confidence = report.confidence
    factors = confidence.factors

    lines: list[str] = [
        f"**可靠程度：{_LEVEL_LABEL.get(confidence.level, confidence.level)}**"
        f"（综合得分 {confidence.overall_score:.2f} / 1.00）",
        "",
        f"- 信息截止时间：**{_fmt_date(confidence.as_of)}**"
        if confidence.as_of
        else "- 信息截止时间：**无法确定**（来源未提供发布时间）",
        *(
            [
                f"- 无来源结论：{confidence.unsourced_conclusion_count} / "
                f"{confidence.total_conclusion_count} 条"
                f"（占比 {confidence.unsourced_ratio:.0%}）"
            ]
            if confidence.total_conclusion_count
            # 「0 / 0 条（占比 0%）」在这个语境下会被读成「全都来源充足」，
            # 而真相是一条结论都没得出来
            else ["- 无来源结论：本次未能得出任何关键结论，下面的分数只反映来源侧的情况"]
        ),
        "",
        "**分项得分**",
        "",
        "| 因子 | 得分 | 权重 |",
        "| --- | --- | --- |",
    ]

    for name, weight in factors.weights.items():
        score = getattr(factors, name, 0.0)
        lines.append(f"| {_FACTOR_LABEL.get(name, name)} | {score:.2f} | {weight:.2f} |")

    raw = factors.raw
    if raw:
        lines.extend(
            [
                "",
                "**原始计数**",
                "",
                f"- 可用来源 {raw.get('readable_sources', 0)} 条"
                f"（共发现 {raw.get('total_sources', 0)} 条，"
                f"独立域名 {raw.get('distinct_domains', 0)} 个）",
                f"- 真正读到网页正文 {raw.get('page_origin_sources', 0)} 条"
                f"（占 {raw.get('page_origin_ratio', 0):.0%}）",
                f"- 抓取成功 {raw.get('fetch_ok', 0)} / 尝试 {raw.get('fetch_attempted', 0)} 条",
                f"- 被引用的来源 {raw.get('cited_readable_sources', 0)} 条",
            ]
        )

    if factors.caps_applied:
        lines.extend(["", "**分数上限（触发原因）**", ""])
        lines.extend(f"- {cap}" for cap in factors.caps_applied)

    if confidence.limitations:
        lines.extend(["", "**局限说明**", "", confidence.limitations])

    return "\n".join(lines)


def render_markdown(report: Report) -> str:
    """把 Report 渲染成完整的 markdown 报告。"""
    parts: list[str] = [f"# {report.title or report.topic}", ""]

    if report.as_of:
        parts.append(f"> 信息截止时间：{_fmt_date(report.as_of)}")
    else:
        parts.append("> 来源未提供发布时间，信息截止时间无法确定")
    parts.append("")
    parts.append(
        f"> 综合可靠程度：**{_LEVEL_LABEL.get(report.confidence.level, '低')}**"
        f"（{report.confidence.overall_score:.2f} / 1.00）"
    )
    parts.append("")

    # ── 摘要 ────────────────────────────────────────────────
    parts.extend(["## 摘要", "", report.executive_summary or "（未生成摘要）", ""])

    # ── 分节正文 ────────────────────────────────────────────
    if report.sections:
        parts.extend(["## 正文", ""])
        for section in report.sections:
            parts.extend([f"### {section.heading}", "", section.body_md, ""])

    # ── 关键结论 ────────────────────────────────────────────
    parts.extend(["## 关键结论", ""])
    if report.key_conclusions:
        for i, conclusion in enumerate(report.key_conclusions, 1):
            if conclusion.sids and conclusion.basis == "sourced":
                citations = "".join(f"[{sid}]" for sid in conclusion.sids)
                parts.append(f"{i}. {conclusion.text} {citations}")
            else:
                # 程序兜底：没有合法来源的结论一律显式标注，不靠 LLM 自觉
                text = conclusion.text
                if not text.startswith(INFERRED_PREFIX):
                    text = f"{INFERRED_PREFIX}{text}"
                parts.append(f"{i}. {text}")
    else:
        parts.append("（本次未能得出有依据的关键结论）")
    parts.append("")

    # ── 遗留问题 ────────────────────────────────────────────
    parts.extend(["## 遗留问题", ""])
    if report.open_questions:
        parts.extend(f"- {question}" for question in report.open_questions)
    else:
        parts.append("- （无）")
    parts.append("")

    # ── 来源列表 ────────────────────────────────────────────
    parts.extend(["## 来源列表", ""])
    if report.sources:
        parts.append(_section_sources(report))
    else:
        parts.append("（本次未能获取任何可用来源）")
    parts.append("")

    # ── 研究过程 ────────────────────────────────────────────
    parts.extend(["## 研究过程记录", "", _process_section(report), ""])

    # ── 置信度说明 ──────────────────────────────────────────
    parts.extend(["## 置信度说明", "", _confidence_section(report), ""])

    return "\n".join(parts).rstrip() + "\n"


def build_source_refs(
    report_sources: list[SourceRef], cited_sids: set[str], section_map: dict[str, list[str]]
) -> list[SourceRef]:
    """回填来源的「被引用情况」—— ``cited`` 与 ``used_in_sections`` 双向对齐。"""
    for source in report_sources:
        source.cited = source.sid in cited_sids
        source.used_in_sections = section_map.get(source.sid, [])
    return report_sources


__all__ = ["build_source_refs", "render_markdown"]
