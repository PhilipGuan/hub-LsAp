"""输出层（S7）：渲染四件套并落盘。

report.md 内含三件：①报告（摘要/正文/关键结论/遗留问题）②来源列表 ④置信度说明；
③研究过程记录 trace.jsonl 由 TraceLogger 在研究过程中实时写入。
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .models import Report, SourceRegistry

_CONF_LABEL = {"high": "高", "medium": "中", "low": "低"}


def make_out_dir(topic: str, root: Path | None = None) -> Path:
    """创建 output/<时间戳>_<主题slug>/ 目录。"""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[\\/:*?\"<>|\s]+", "-", topic.strip())[:40].strip("-") or "research"
    out_dir = (root or Path("output")) / f"{stamp}_{slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def clean_refs(refs: list[str] | None, registry: SourceRegistry) -> list[str]:
    """兜底校验：只保留登记表中真实存在的 sid（防幻觉引用）。"""
    valid = registry.valid_sids()
    cleaned: list[str] = []
    for ref in refs or []:
        if ref in valid and ref not in cleaned:
            cleaned.append(ref)
    return cleaned


def render_report(report: Report, registry: SourceRegistry) -> str:
    lines: list[str] = [f"# {report.title}", "", "## 摘要", "", report.summary.strip() or "（无）", ""]

    lines += ["## 正文", ""]
    for section in report.sections:
        lines.append(f"### {section.heading}")
        lines.append("")
        lines.append(section.body.strip() or "（无）")
        refs = clean_refs(section.refs, registry)
        if refs:
            lines += ["", f"来源：{', '.join(refs)}"]
        lines.append("")

    lines += ["## 关键结论", ""]
    inference_count = 0
    for i, conclusion in enumerate(report.key_conclusions, start=1):
        refs = clean_refs(conclusion.refs, registry)
        if refs:
            label = _CONF_LABEL.get(conclusion.confidence, conclusion.confidence)
            tag = f"（来源：{', '.join(refs)}｜置信度：{label}）"
        else:
            inference_count += 1
            tag = "（模型推断｜无直接来源，建议核实）"
        lines.append(f"{i}. {conclusion.text.strip()} {tag}")

    lines += ["", "## 遗留问题", ""]
    if report.open_questions:
        lines += [f"- {q}" for q in report.open_questions]
    else:
        lines.append("- （无）")

    lines += ["", "## 置信度说明", ""]
    for note in report.confidence_notes:
        lines.append(f"- {note}")
    if inference_count:
        lines.append(f"- {inference_count} 条关键结论无来源支撑，已标注为“模型推断”")
    cutoff = registry.latest_date() or report.info_cutoff or "未知，建议核实时效"
    lines.append(f"- 信息截止时间：{cutoff}")

    lines += ["", "## 来源列表", ""]
    sources = registry.all_sources()
    if sources:
        lines += ["| 编号 | 标题 | URL |", "| --- | --- | --- |"]
        for s in sources:
            lines.append(f"| {s.sid} | {s.title} | {s.url} |")
    else:
        lines.append("（本次研究未登记任何来源）")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(report: Report, registry: SourceRegistry, out_dir: Path) -> Path:
    """落盘 report.md（trace.jsonl 已实时写入），返回报告路径。"""
    report_path = out_dir / "report.md"
    report_path.write_text(render_report(report, registry), encoding="utf-8")
    return report_path
