"""WriterAgent 综合:带行内引用生成 Markdown 报告,并转自包含 HTML。

来源列表由代码确定性追加(不交给 LLM),保证引用编号与列表一一对应。
"""
import datetime

import markdown

from config import CONFIG

from .base import BaseAgent, sources_digest


class WriterAgent(BaseAgent):
    name = "writer"

    async def run(self, topic: str, sub_questions: list, sources: list) -> str:
        await self.send("status", message="信息收集完成,正在综合生成研究报告…")
        subs = "\n".join(f"{i}. {q}" for i, q in enumerate(sub_questions, 1))
        digest = sources_digest(sources, cap=400)
        if not digest:
            digest = "(本次研究未获取到任何来源,请在报告中如实说明,结论全部标注「模型推断」。)"
        system = "你是资深研究分析师,基于给定来源要点撰写严谨、可追溯的研究报告。"
        user = (
            f"# 任务:撰写研究报告\n\n研究主题:{topic}\n\n子问题:\n{subs}\n\n"
            f"## 可用来源(引用时只能使用方括号内编号,如 [1][3])\n{digest}\n\n"
            "## 写作要求\n"
            f"1. 语言与主题一致;结构固定:\n"
            f"   # 研究报告:{topic}\n"
            "   ## 摘要\n   ## 分析正文(按子问题分 ### 小节)\n"
            "   ## 关键结论(编号列表,每条带 [n] 引用)\n   ## 遗留问题\n"
            "2. 所有事实性结论必须来自来源要点并标注 [n];多来源合并标注如 [1][2]。\n"
            "3. 来源不足以支撑、但你认为重要的判断,必须在句末标注「模型推断」。\n"
            "4. 不编造数据;来源互相矛盾时如实呈现分歧并各自标注引用。\n"
            '5. 不要自己生成"来源列表"(系统会自动追加),正文只负责引用 [n]。\n'
        )
        raw = await self.call_llm(system, user, temperature=0.3, max_tokens=8192)
        md = raw.strip()
        await self.send("status", message="报告生成完成")
        return self.append_sources(md, sources)

    @staticmethod
    def append_sources(md: str, sources: list) -> str:
        """确定性追加来源列表,与引用编号严格对应。"""
        lines = ["", "---", "## 来源列表", ""]
        if not sources:
            lines.append("本次研究未获取到可用来源。")
        for s in sources:
            lines.append(f"{s.sid}. [{s.title}]({s.url}) — {s.domain}")
        lines += ["", "> 说明:标注「模型推断」的结论无直接来源支撑,请谨慎采用。"]
        return md.rstrip() + "\n" + "\n".join(lines) + "\n"

    HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TOPIC}}</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px 16px; background: #f7f8fa; color: #1f2933;
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
    line-height: 1.75; font-size: 15.5px;
  }
  article {
    max-width: 800px; margin: 0 auto; background: #fff; padding: 40px 48px;
    border: 1px solid #e5e7eb; border-radius: 12px;
    box-shadow: 0 1px 3px rgba(16, 24, 40, .06);
  }
  h1 { font-size: 26px; line-height: 1.4; margin: 0 0 8px; }
  h2 { font-size: 20px; margin: 32px 0 12px; padding-bottom: 8px; border-bottom: 2px solid #e5e7eb; }
  h3 { font-size: 17px; margin: 24px 0 8px; }
  a { color: #2563eb; text-decoration: none; word-break: break-all; }
  a:hover { text-decoration: underline; }
  blockquote {
    margin: 16px 0; padding: 8px 16px; background: #f3f4f6;
    border-left: 4px solid #9ca3af; color: #4b5563; border-radius: 0 6px 6px 0;
  }
  table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14px; }
  th, td { border: 1px solid #d1d5db; padding: 6px 10px; text-align: left; }
  th { background: #f3f4f6; }
  code { background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 13.5px; }
  ol, ul { padding-left: 24px; }
  li { margin: 4px 0; }
  hr { border: none; border-top: 1px solid #e5e7eb; margin: 28px 0; }
  footer { max-width: 800px; margin: 16px auto 0; color: #6b7280; font-size: 12.5px; text-align: center; }
  @media print { body { background: #fff; padding: 0; } article { border: none; box-shadow: none; } }
</style>
</head>
<body>
<article>
{{BODY}}
</article>
<footer>{{META}}</footer>
</body>
</html>
"""

    def build_html(self, md: str, topic: str, stats: dict) -> str:
        """Markdown → 自包含 HTML(内联样式,可离线打开/直接分享)。"""
        body = markdown.markdown(md, extensions=["tables", "fenced_code", "nl2br"])
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        meta = (
            f"深度研究助手 · 生成时间 {now} · 研究轮数 {stats['rounds']}/{CONFIG.max_rounds}"
            f" · 搜索 {stats['searches']} 次 · 抓取正文 {stats['pages_fetched']} 页"
            f"(降级摘要 {stats['fallbacks']} 页) · 跳过 {stats['skipped']} 项"
        )
        html = self.HTML_TEMPLATE.replace("{{TITLE}}", topic).replace("{{TOPIC}}", topic)
        return html.replace("{{BODY}}", body).replace("{{META}}", meta)
