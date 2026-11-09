"""
报告综合模块

使用 LLM API（支持 Claude/GLM等）整合所有信息，生成最终的研究报告
"""

import json
import os
import time
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from loguru import logger
import openai
from pathlib import Path


@dataclass
class ResearchReport:
    """研究报告数据类"""
    title: str
    abstract: str
    sections: List[Dict[str, Any]]
    conclusions: List[str]
    remaining_questions: List[str]
    sources: List[Dict[str, Any]]
    confidence_level: str
    cutoff_date: str
    metadata: Dict[str, Any]


class ReportSynthesizer:
    """报告生成器"""

    def __init__(self, api_key: Optional[str] = None):
        # 优先使用 Claude API
        self.api_key = api_key or os.getenv("CLAUDE_API_KEY")
        self.base_url = "https://api.anthropic.com/v1"
        self.model = "claude-3-opus-20240229"

        # 如果没有 Claude API，尝试其他 LLM
        if not self.api_key:
            self.api_key = os.getenv("LLM_API_KEY")
            self.base_url = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
            self.model = os.getenv("LLM_MODEL", "glm-4v")

        if not self.api_key:
            raise ValueError("需要提供 API Key（CLAUDE_API_KEY 或 LLM_API_KEY）")

        # 初始化 OpenAI 客户端
        self.client = openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

        # 报告模板
        self.report_template = {
            "structure": {
                "abstract_length": "200-300字",
                "section_count": 5-8,
                "conclusion_count": 5-10,
                "source_count": 10-20
            },
            "style": {
                "formal": True,
                "objective": True,
                "evidence_based": True
            }
        }

    async def generate_report(
        self,
        topic: str,
        contents: List[Dict[str, Any]],
        sub_questions: List[Dict[str, Any]],
        session_id: str
    ) -> ResearchReport:
        """
        生成综合研究报告

        Args:
            topic: 研究主题
            contents: 收集到的内容列表
            sub_questions: 子问题列表
            session_id: 会话ID

        Returns:
            研究报告
        """
        logger.info("开始生成研究报告...")

        # 准备提示词
        prompt = self._build_prompt(topic, contents, sub_questions)

        # 调用 LLM API
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": """你是一位专业的研究分析师，擅长深度调研和撰写结构化研究报告。
                    请基于提供的信息生成一份高质量的研究报告，包含摘要、分节正文、关键结论和遗留问题。
                    确保内容客观、准确，并明确标注信息来源和置信度。"""
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=4000,
            temperature=0.3
        )

        # 解析响应
        report_content = response.choices[0].message.content

        # 解析报告结构
        report = self._parse_report_content(
            report_content,
            topic,
            contents
        )

        # 添加元数据
        report.metadata = {
            "session_id": session_id,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "content_count": len(contents),
            "sub_questions_count": len(sub_questions)
        }

        logger.info("研究报告生成完成")
        return report

    def _build_prompt(
        self,
        topic: str,
        contents: List[Dict[str, Any]],
        sub_questions: List[Dict[str, Any]]
    ) -> str:
        """构建提示词"""
        # 构建内容摘要
        content_summaries = []
        for i, content in enumerate(contents[:20], 1):  # 限制内容数量
            summary = f"""
来源 {i}:
标题: {content.get('title', '无标题')}
URL: {content.get('url', '')}
网站: {content.get('site_name', '未知')}
发布日期: {content.get('date_published', '未知')}
摘要: {content.get('snippet', '无摘要')[:300]}...
"""
            content_summaries.append(summary)

        # 构建子问题列表
        questions_text = "\n".join([
            f"{q.get('id', '')}: {q.get('question', '')}"
            for q in sub_questions
        ])

        prompt = f"""
请基于以下信息为"{topic}"生成一份结构化研究报告：

== 研究子问题 ==
{questions_text}

== 收集到的内容 ==
{''.join(content_summaries)}

== 报告要求 ==
1. 生成一份包含以下部分的研究报告：
   - 摘要（200-300字）
   - 分节正文（5-8个章节）
   - 关键结论（5-10条）
   - 遗留问题（3-5条）
   - 来源列表

2. 报告要求：
   - 内容客观、准确，避免主观臆断
   - 关键论点需要信息来源支撑
   - 对于无法确认的信息，标注"模型推断"
   - 每个重要结论需要关联1-3个来源

3. 格式要求：
   - 使用Markdown格式
   - 各部分使用明确的标题
   - 来源使用引用格式
   - 重要内容加粗强调

请开始生成报告：
"""

        return prompt

    def _parse_report_content(
        self,
        content: str,
        topic: str,
        contents: List[Dict[str, Any]]
    ) -> ResearchReport:
        """解析报告内容"""
        # 这里简化处理，实际应该更精细地解析Markdown结构
        lines = content.split('\n')

        # 查找摘要
        abstract = ""
        in_abstract = False
        for line in lines:
            if "摘要" in line or "Abstract" in line:
                in_abstract = True
                continue
            elif line.startswith('#') and in_abstract:
                break
            elif in_abstract and line.strip():
                abstract += line + '\n'

        # 提取各部分内容
        sections = self._extract_sections(content)
        conclusions = self._extract_conclusions(content)
        remaining_questions = self._extract_remaining_questions(content)
        sources = self._extract_sources(contents)

        # 计算置信度
        confidence_level = self._calculate_confidence(contents)

        # 确定信息截止时间
        cutoff_date = self._determine_cutoff_date(contents)

        return ResearchReport(
            title=f"{topic} 研究报告",
            abstract=abstract.strip(),
            sections=sections,
            conclusions=conclusions,
            remaining_questions=remaining_questions,
            sources=sources,
            confidence_level=confidence_level,
            cutoff_date=cutoff_date,
            metadata={}
        )

    def _extract_sections(self, content: str) -> List[Dict[str, Any]]:
        """提取报告章节"""
        sections = []
        lines = content.split('\n')

        current_section = None
        section_content = []

        for line in lines:
            # 检查章节标题
            if line.startswith('##') or line.startswith('###'):
                # 保存上一个章节
                if current_section:
                    sections.append({
                        'title': current_section,
                        'content': '\n'.join(section_content).strip()
                    })

                # 开始新章节
                current_section = line.lstrip('#').strip()
                section_content = []
            elif current_section:
                section_content.append(line)

        # 添加最后一个章节
        if current_section:
            sections.append({
                'title': current_section,
                'content': '\n'.join(section_content).strip()
            })

        return sections

    def _extract_conclusions(self, content: str) -> List[str]:
        """提取结论"""
        conclusions = []
        lines = content.split('\n')

        in_conclusions = False
        for line in lines:
            if "结论" in line or "Conclusion" in line:
                in_conclusions = True
                continue
            elif line.startswith('#') and in_conclusions:
                break
            elif in_conclusions and line.strip():
                # 去掉序号，提取实质内容
                conclusion = line.strip()
                if conclusion.startswith(('1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.', '10.')):
                    conclusion = conclusion.split('.', 1)[1].strip()
                elif re.match(r'^\d+\.$', conclusion):
                    continue
                if conclusion:
                    conclusions.append(conclusion)

        return conclusions[:10]  # 最多10个结论

    def _extract_remaining_questions(self, content: str) -> List[str]:
        """提取遗留问题"""
        questions = []
        lines = content.split('\n')

        in_questions = False
        for line in lines:
            if "遗留问题" in line or "Remaining Questions" in line:
                in_questions = True
                continue
            elif line.startswith('#') and in_questions:
                break
            elif in_questions and line.strip():
                question = line.strip()
                if question.startswith(('•', '-', '*')):
                    question = question[1:].strip()
                if question:
                    questions.append(question)

        return questions[:5]  # 最多5个问题

    def _extract_sources(self, contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """提取来源列表"""
        sources = []
        for content in contents:
            source = {
                "title": content.get('title', '无标题'),
                "url": content.get('url', ''),
                "site_name": content.get('site_name', '未知来源'),
                "date_published": content.get('date_published', '未知'),
                "relevance_score": self._calculate_source_relevance(content)
            }
            sources.append(source)

        # 按相关性排序
        sources.sort(key=lambda x: x['relevance_score'], reverse=True)
        return sources[:20]  # 最多20个来源

    def _calculate_source_relevance(self, content: Dict[str, Any]) -> float:
        """计算来源相关性得分"""
        score = 0
        title = content.get('title', '').lower()
        snippet = content.get('snippet', '').lower()

        # 标题相关性
        if len(title) > 50:
            score += 40
        elif len(title) > 20:
            score += 20

        # 摘要相关性
        if len(snippet) > 200:
            score += 30
        elif len(snippet) > 100:
            score += 15

        # 来源可信度
        site_name = content.get('site_name', '').lower()
        if any(source in site_name for source in ['wikipedia', 'github', 'stackoverflow']):
            score += 20
        elif any(source in site_name for source in ['blog', 'article', 'tech']):
            score += 10

        # 新鲜度
        date_published = content.get('date_published')
        if date_published:
            try:
                from dateutil.parser import parse
                from datetime import datetime
                publish_date = parse(date_published)
                age_days = (datetime.now() - publish_date).days
                if age_days <= 30:
                    score += 10
                elif age_days <= 90:
                    score += 5
            except:
                pass

        return min(score, 100)

    def _calculate_confidence(self, contents: List[Dict[str, Any]]) -> str:
        """计算置信度级别"""
        if not contents:
            return "未知"

        # 计算高质量内容的比例
        high_quality = 0
        for content in contents:
            if content.get('content', ''):
                high_quality += 1

        quality_ratio = high_quality / len(contents)

        if quality_ratio >= 0.8:
            return "高"
        elif quality_ratio >= 0.5:
            return "中"
        else:
            return "低"

    def _determine_cutoff_date(self, contents: List[Dict[str, Any]]) -> str:
        """确定信息截止时间"""
        dates = []
        for content in contents:
            date_str = content.get('date_published')
            if date_str:
                try:
                    from dateutil.parser import parse
                    dates.append(parse(date_str))
                except:
                    continue

        if dates:
            latest_date = max(dates)
            return latest_date.strftime("%Y年%m月%d日")
        else:
            return "未知"

    def save_report(self, report: ResearchReport, session_id: str) -> str:
        """保存研究报告"""
        output_dir = Path("data/output") / session_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # 保存完整报告
        report_file = output_dir / "research_report.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=2)

        # 保存 Markdown 格式报告
        markdown_file = output_dir / "research_report.md"
        with open(markdown_file, 'w', encoding='utf-8') as f:
            f.write(self._format_markdown_report(report))

        logger.info(f"研究报告已保存: {report_file}")
        return str(report_file)

    def _format_markdown_report(self, report: ResearchReport) -> str:
        """格式化为 Markdown 报告"""
        markdown = f"# {report.title}\n\n"

        # 摘要
        markdown += "## 摘要\n\n"
        markdown += f"{report.abstract}\n\n"

        # 正文章节
        markdown += "## 正文\n\n"
        for section in report.sections:
            markdown += f"### {section['title']}\n\n"
            markdown += f"{section['content']}\n\n"

        # 关键结论
        markdown += "## 关键结论\n\n"
        for i, conclusion in enumerate(report.conclusions, 1):
            markdown += f"{i}. {conclusion}\n"

        # 遗留问题
        markdown += "\n## 遗留问题\n\n"
        for question in report.remaining_questions:
            markdown += f"- {question}\n"

        # 来源列表
        markdown += "\n## 来源列表\n\n"
        for source in report.sources[:10]:  # 显示前10个来源
            markdown += f"- [{source['title']}]({source['url']}) - {source['site_name']}\n"

        # 置信度说明
        markdown += f"\n## 置信度说明\n\n"
        markdown += f"- 置信度级别：{report.confidence_level}\n"
        markdown += f"- 信息截止时间：{report.cutoff_date}\n"

        return markdown