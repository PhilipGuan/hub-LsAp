"""落盘：把 ResearchRecord 写成 report.json / report.md / report.html 三件套。

- report.json 是唯一数据源（四类产物合一）
- report.md   由 JSON 渲染导出的人读 Markdown
- report.html 单文件内联样式，四类产物一页，含来源引用
"""
from __future__ import annotations

import html
import re
from pathlib import Path

from .. import config
from ..models import ResearchRecord

# 置信度等级对应颜色
_LEVEL_COLOR = {"high": "#16a34a", "medium": "#d97706", "low": "#dc2626"}


def output_dir(record: ResearchRecord) -> Path:
    """记录对应的产物目录。"""
    return config.OUTPUT_DIR / record.id


def write(record: ResearchRecord) -> Path:
    """落盘三件套，返回产物目录。"""
    d = output_dir(record)
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    (d / "report.md").write_text(to_markdown(record), encoding="utf-8")
    (d / "report.html").write_text(to_html(record), encoding="utf-8")
    return d


# --------------------------------------------------------------------------
# Markdown 渲染
# --------------------------------------------------------------------------

def to_markdown(record: ResearchRecord) -> str:
    """把记录渲染为人读 Markdown。"""
    c = record.confidence
    lines: list[str] = [
        f"# {record.topic} —— 深度研究报告",
        "",
        f"> 生成时间：{record.created_at} ｜ 置信度：**{c.level}** ｜ 信息截止：{c.cutoff or '未知'}",
        "",
        "## 摘要",
        record.summary or "（无）",
        "",
        "## 正文",
    ]
    for sec in record.sections:
        lines.append(f"### {sec.heading}")
        lines.append(sec.content)
        lines.append("")

    lines.append("## 关键结论")
    for item in record.key_conclusions:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## 遗留问题")
    if record.open_questions:
        for q in record.open_questions:
            lines.append(f"- {q}")
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 来源列表")
    for i, s in enumerate(record.sources):
        lines.append(f"- [来源{i}] [{s.title}]({s.url}) — {s.site}，{s.published_at}")
    lines.append("")

    lines.append("## 置信度说明")
    lines.append(f"- 等级：{c.level}")
    lines.append(f"- 信息截止：{c.cutoff or '未知'}")
    for n in c.notes:
        lines.append(f"- {n}")
    lines.append("")

    lines.append("## 研究过程")
    lines.append(
        f"- 拆分子问题 {len(record.process.sub_questions)} 个，检索 {record.process.iterations} 轮"
    )
    for sq in record.process.sub_questions:
        lines.append(f"  - {sq.question}")
    for r in record.process.rounds:
        lines.append(
            f"- 第 {r.round_no} 轮（{r.purpose}）：关键词 {r.queries}，"
            f"读 {len(r.pages_read)} 页，抽 {len(r.extracted)} 条证据"
        )
        if r.decision:
            lines.append(f"  - 补检判定：{r.decision}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# HTML 渲染
# --------------------------------------------------------------------------

_CSS = """
:root { --bg:#f7f7f8; --card:#fff; --text:#1f2328; --muted:#656d76; --border:#e5e7eb; --accent:#2563eb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --card:#161b22; --text:#e6edf3; --muted:#8b949e; --border:#30363d; --accent:#58a6ff; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; }
main { max-width:820px; margin:0 auto; padding:40px 20px 80px; }
header h1 { font-size:1.9em; margin:0 0 12px; }
.meta { display:flex; flex-wrap:wrap; gap:10px; align-items:center; color:var(--muted); font-size:.9em; margin-bottom:24px; }
.badge { color:#fff; padding:2px 10px; border-radius:999px; font-size:.85em; }
.sec { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px 24px; margin-bottom:16px; }
.sec h2 { margin:0 0 12px; font-size:1.2em; border-left:4px solid var(--accent); padding-left:10px; }
.sec h4 { margin:18px 0 8px; }
.sec p { margin:8px 0; }
ul, ol { margin:8px 0; padding-left:22px; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
a.src { font-size:.78em; vertical-align:super; white-space:nowrap; }
.sources li { margin:6px 0; }
.sources .site, .sources .time { color:var(--muted); font-size:.85em; margin-left:8px; }
details { margin:10px 0; }
summary { cursor:pointer; font-weight:600; }
code { background:var(--border); padding:1px 5px; border-radius:4px; font-size:.9em; }
"""


def to_html(record: ResearchRecord) -> str:
    """渲染单文件 HTML（内联样式，四类产物一页，含来源引用）。"""
    color = _LEVEL_COLOR.get(record.confidence.level, "#6b7280")
    sections_html = "\n".join(
        f'<section class="sec"><h2>{html.escape(sec.heading)}</h2>{_md_to_html(sec.content)}</section>'
        for sec in record.sections
    )
    conclusions_html = "".join(
        f"<li>{_render_inline(c)}</li>" for c in record.key_conclusions
    )
    open_q_html = "".join(f"<li>{_render_inline(q)}</li>" for q in record.open_questions)
    if not open_q_html:
        open_q_html = "<li>（无）</li>"

    sources_html = "".join(
        f'<li id="src-{i}"><a href="{html.escape(s.url)}" target="_blank" rel="noopener">'
        f"{html.escape(s.title)}</a>"
        f'<span class="site">{html.escape(s.site)}</span>'
        f'<span class="time">{html.escape(s.published_at)}</span></li>'
        for i, s in enumerate(record.sources)
    )

    conf_notes = "".join(f"<li>{html.escape(n)}</li>" for n in record.confidence.notes)
    conf_html = (
        f"<li>等级：<strong>{html.escape(record.confidence.level)}</strong></li>"
        f"<li>信息截止：{html.escape(record.confidence.cutoff or '未知')}</li>{conf_notes}"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(record.topic)} — 深度研究报告</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<header>
  <h1>{html.escape(record.topic)}</h1>
  <div class="meta">
    <span class="badge" style="background:{color}">置信度 {html.escape(record.confidence.level)}</span>
    <span>生成时间 {html.escape(record.created_at)}</span>
    <span>信息截止 {html.escape(record.confidence.cutoff or '未知')}</span>
  </div>
</header>

<section class="sec">
  <h2>摘要</h2>
  <p>{html.escape(record.summary)}</p>
</section>

{sections_html}

<section class="sec">
  <h2>关键结论</h2>
  <ul>{conclusions_html}</ul>
</section>

<section class="sec">
  <h2>遗留问题</h2>
  <ul>{open_q_html}</ul>
</section>

<section class="sec">
  <h2>来源列表</h2>
  <ol class="sources">{sources_html}</ol>
</section>

<section class="sec">
  <h2>置信度说明</h2>
  <ul>{conf_html}</ul>
</section>

<section class="sec">
  <h2>研究过程</h2>
  {_process_html(record)}
</section>
</main>
</body>
</html>"""


def _process_html(record: ResearchRecord) -> str:
    """渲染研究过程（子问题 + 每轮检索详情）。"""
    p = record.process
    parts = [
        f"<p>拆分子问题 {len(p.sub_questions)} 个，检索 {p.iterations} 轮。</p>",
        "<ul>" + "".join(
            f"<li><strong>{html.escape(sq.question)}</strong>"
            + (f" — {html.escape(sq.rationale)}" if sq.rationale else "")
            + "</li>"
            for sq in p.sub_questions
        ) + "</ul>",
    ]
    for r in p.rounds:
        detail = (
            f"<details><summary>第 {r.round_no} 轮（{html.escape(r.purpose)}）："
            f"{len(r.queries)} 个关键词 · 读 {len(r.pages_read)} 页 · 抽 {len(r.extracted)} 条证据</summary>"
            f"<ul><li>关键词：{html.escape('、'.join(r.queries))}</li>"
        )
        if r.extracted:
            detail += "<li>抽取证据：<ul>" + "".join(
                "<li>" + _render_inline(ev.claim) + " "
                + (
                    f'<a class="src" href="#src-{ev.source_index}">[来源{ev.source_index}]</a>'
                    if ev.source_index is not None
                    else '<em>[模型推断]</em>'
                )
                + "</li>"
                for ev in r.extracted
            ) + "</ul></li>"
        if r.decision:
            detail += f"<li>补检判定：{html.escape(r.decision)}</li>"
        detail += "</ul></details>"
        parts.append(detail)
    return "\n".join(parts)


def _render_inline(text: str) -> str:
    """行内渲染：转义 + [来源N]→链接 + **加粗**。"""
    text = html.escape(text)
    text = re.sub(r"\[来源(\d+)\]", r'<a class="src" href="#src-\1">[\1]</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return text


def _md_to_html(text: str) -> str:
    """极简 markdown → HTML（标题、加粗、列表、段落）。"""
    lines = text.splitlines()
    out: list[str] = []
    stack: list[str] = []

    def close_lists() -> None:
        while stack:
            out.append(f"</{stack.pop()}>")

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            close_lists()
            continue
        if stripped.startswith("### "):
            close_lists()
            out.append(f"<h4>{_render_inline(stripped[4:])}</h4>")
        elif stripped.startswith("## "):
            close_lists()
            out.append(f"<h4>{_render_inline(stripped[3:])}</h4>")
        elif stripped.startswith("# "):
            close_lists()
            out.append(f"<h4>{_render_inline(stripped[2:])}</h4>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not stack or stack[-1] != "ul":
                close_lists()
                stack.append("ul")
                out.append("<ul>")
            out.append(f"<li>{_render_inline(stripped[2:])}</li>")
        elif re.match(r"^\d+\.\s", stripped):
            if not stack or stack[-1] != "ol":
                close_lists()
                stack.append("ol")
                out.append("<ol>")
            content = re.sub(r"^\d+\.\s", "", stripped)
            out.append(f"<li>{_render_inline(content)}</li>")
        else:
            close_lists()
            out.append(f"<p>{_render_inline(stripped)}</p>")
    close_lists()
    return "\n".join(out)
