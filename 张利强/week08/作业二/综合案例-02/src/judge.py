"""
补检判断模块

评估已收集内容的质量，决定是否需要补充检索
"""

import json
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from loguru import logger


@dataclass
class ContentQuality:
    """内容质量评估结果"""
    content_id: str
    score: float  # 0-100
    reliability: str  # "high", "medium", "low"
    completeness: str  # "excellent", "good", "partial", "poor"
    freshness: str  # "recent", "moderate", "outdated"
    issues: List[str]
    strengths: List[str]


class ContentJudge:
    """内容质量评估器"""

    def __init__(self):
        # 质量评估阈值
        self.thresholds = {
            "score_good": 70,
            "score_medium": 40,
            "reliable_sources": ["wikipedia", "github", "stackoverflow", "stackoverflow.com",
                               "medium", "dev.to", "hackernoon", "techcrunch", "reuters",
                               "bloomberg", "fortune", "forbes"],
            "recent_threshold": 365 * 24 * 3600  # 1年内的内容为近期
        }

    def evaluate_content(self, content: Dict[str, Any]) -> ContentQuality:
        """
        评估单条内容的质量

        Args:
            content: 内容数据（包含 title, url, snippet, content, date_published 等）

        Returns:
            质量评估结果
        """
        content_id = content.get("id", hash(content.get("url", "")))

        # 计算各项指标
        source_score = self._evaluate_source(content)
        content_score = self._evaluate_content_length(content)
        date_score = self._evaluate_freshness(content)
        diversity_score = self._evaluate_diversity(content)
        completeness_score = self._evaluate_completeness(content)

        # 综合评分
        total_score = (
            source_score * 0.3 +
            content_score * 0.25 +
            date_score * 0.2 +
            diversity_score * 0.15 +
            completeness_score * 0.1
        )

        # 确定可靠性等级
        reliability = self._determine_reliability(total_score, content)

        # 确定完整度
        completeness = self._determine_completeness(completeness_score)

        # 确定新鲜度
        freshness = self._determine_freshness(content)

        # 识别问题和优势
        issues, strengths = self._identify_issues_strengths(
            content, total_score, source_score, content_score
        )

        return ContentQuality(
            content_id=content_id,
            score=round(total_score, 1),
            reliability=reliability,
            completeness=completeness,
            freshness=freshness,
            issues=issues,
            strengths=strengths
        )

    def evaluate_batch(self, contents: List[Dict[str, Any]]) -> List[ContentQuality]:
        """
        批量评估内容质量

        Args:
            contents: 内容数据列表

        Returns:
            质量评估结果列表
        """
        results = []

        for content in contents:
            try:
                quality = self.evaluate_content(content)
                results.append(quality)
            except Exception as e:
                logger.error(f"评估内容失败: {str(e)}")
                # 创建一个最低评分的结果
                results.append(ContentQuality(
                    content_id=content.get("id", "unknown"),
                    score=0,
                    reliability="low",
                    completeness="poor",
                    freshness="unknown",
                    issues=["评估过程中出错"],
                    strengths=[]
                ))

        return results

    def _evaluate_source(self, content: Dict[str, Any]) -> float:
        """评估来源可信度"""
        url = content.get("url", "")
        site_name = content.get("site_name", "").lower()

        # 检查是否为可信来源
        if any(source in site_name for source in self.thresholds["reliable_sources"]):
            return 90

        # 检查商业或技术博客
        tech_patterns = ["blog", "article", "tech", "developer", "programming", "code"]
        if any(pattern in site_name for pattern in tech_patterns):
            return 75

        # 检查个人博客或论坛
        personal_patterns = ["blogspot", "medium.com", "dev.to", "github.io"]
        if any(pattern in site_name for pattern in personal_patterns):
            return 60

        # 默认评分
        return 50

    def _evaluate_content_length(self, content: Dict[str, Any]) -> float:
        """评估内容长度和质量"""
        snippet = content.get("snippet", "")
        summary = content.get("summary", "")
        full_content = content.get("content", "")

        # 以字符数作为长度指标
        full_length = len(full_content)
        snippet_length = len(snippet)
        summary_length = len(summary)

        # 如果只有摘要，使用摘要长度
        if not full_content and summary:
            full_content = summary
            full_length = summary_length

        # 根据长度评分
        if full_length > 3000:
            return 90
        elif full_length > 1500:
            return 75
        elif full_length > 500:
            return 60
        elif full_length > 200:
            return 40
        else:
            return 20

    def _evaluate_freshness(self, content: Dict[str, Any]) -> float:
        """评估内容新鲜度"""
        date_published = content.get("date_published")

        if not date_published:
            # 没有发布日期，无法判断
            return 50

        try:
            import datetime
            from dateutil.parser import parse

            # 解析发布日期
            publish_date = parse(date_published)
            now = datetime.datetime.now(publish_date.tzinfo)
            age_days = (now - publish_date).days

            # 根据年龄评分
            if age_days <= 30:
                return 95
            elif age_days <= 90:
                return 80
            elif age_days <= 180:
                return 65
            elif age_days <= 365:
                return 50
            else:
                return 30

        except Exception:
            # 解析失败，默认评分
            return 50

    def _evaluate_diversity(self, content: Dict[str, Any]) -> float:
        """评估内容多样性"""
        snippet = content.get("snippet", "")
        full_content = content.get("content", "")

        # 检查内容是否包含多个方面的信息
        diversity_indicators = [
            r"\d+",  # 数字
            r"\d+\%",  # 百分比
            r"\d+\.\d+",  # 小数
            r"例子", "例如", "比如", "如", "for example",
            "优点", "缺点", "优势", "劣势", "pros", "cons",
            "比较", "对比", "compare", "versus",
            "趋势", "趋势分析", "trend",
            "数据", "statistics", "数据支撑"
        ]

        score = 0
        for indicator in diversity_indicators:
            if re.search(indicator, snippet, re.IGNORECASE):
                score += 10

        return min(score, 100)

    def _evaluate_completeness(self, content: Dict[str, Any]) -> float:
        """评估内容完整度"""
        # 检查是否包含常见的内容元素
        content_elements = {
            "标题": bool(content.get("title")),
            "摘要": bool(content.get("snippet")),
            "正文": bool(content.get("content")),
            "来源": bool(content.get("site_name")),
            "链接": bool(content.get("url")),
            "发布日期": bool(content.get("date_published"))
        }

        present_elements = sum(1 for element in content_elements.values() if element)
        total_elements = len(content_elements)

        return (present_elements / total_elements) * 100

    def _determine_reliability(self, score: float, content: Dict[str, Any]) -> str:
        """确定可靠性等级"""
        if score >= self.thresholds["score_good"]:
            return "high"
        elif score >= self.thresholds["score_medium"]:
            return "medium"
        else:
            return "low"

    def _determine_completeness(self, score: float) -> str:
        """确定完整度等级"""
        if score >= 80:
            return "excellent"
        elif score >= 60:
            return "good"
        elif score >= 40:
            return "partial"
        else:
            return "poor"

    def _determine_freshness(self, content: Dict[str, Any]) -> str:
        """确定新鲜度等级"""
        date_published = content.get("date_published")

        if not date_published:
            return "unknown"

        try:
            import datetime
            from dateutil.parser import parse

            publish_date = parse(date_published)
            now = datetime.datetime.now(publish_date.tzinfo)
            age_days = (now - publish_date).days

            if age_days <= 90:
                return "recent"
            elif age_days <= 365:
                return "moderate"
            else:
                return "outdated"

        except Exception:
            return "unknown"

    def _identify_issues_strengths(
        self,
        content: Dict[str, Any],
        total_score: float,
        source_score: float,
        content_score: float
    ) -> tuple[List[str], List[str]]:
        """识别内容和问题"""
        issues = []
        strengths = []

        # 根据总评分
        if total_score < 40:
            issues.append("内容质量较低，建议补充更多来源")
        elif total_score >= 80:
            strengths.append("内容质量优秀")

        # 根据来源评分
        if source_score < 50:
            issues.append("来源可信度较低")
        elif source_score >= 80:
            strengths.append("来源较为可信")

        # 根据内容长度
        if content_score < 40:
            issues.append("内容过于简略，深度不足")
        elif content_score >= 80:
            strengths.append("内容详细丰富")

        # 检查具体问题
        if not content.get("content"):
            issues.append("缺少正文内容")
        if not content.get("date_published"):
            issues.append("缺少发布日期")

        # 检查优势
        if content.get("title"):
            strengths.append("标题清晰明确")
        if len(content.get("snippet", "")) > 200:
            strengths.append("摘要内容充实")

        return issues, strengths

    def should_supplement(
        self,
        contents: List[Dict[str, Any]],
        threshold: float = 60.0
    ) -> Dict[str, Any]:
        """
        判断是否需要补充检索

        Args:
            contents: 已收集的内容列表
            threshold: 补充检索的阈值（平均分）

        Returns:
            决策结果
        """
        if not contents:
            return {
                "should_supplement": True,
                "reason": "没有收集到任何内容",
                "avg_score": 0,
                "low_count": 0
            }

        # 评估所有内容
        qualities = self.evaluate_batch(contents)
        avg_score = sum(q.score for q in qualities) / len(qualities)
        low_count = sum(1 for q in qualities if q.score < 40)

        # 决策逻辑
        if avg_score < threshold:
            return {
                "should_supplement": True,
                "reason": f"平均分 {avg_score:.1f} 低于阈值 {threshold}",
                "avg_score": avg_score,
                "low_count": low_count
            }
        elif low_count > len(contents) * 0.5:  # 超过50%的内容质量较低
            return {
                "should_supplement": True,
                "reason": f"超过50%的内容质量较低（{low_count}/{len(qualities)}）",
                "avg_score": avg_score,
                "low_count": low_count
            }
        else:
            return {
                "should_supplement": False,
                "reason": "内容质量足够，可以进入综合阶段",
                "avg_score": avg_score,
                "low_count": low_count
            }