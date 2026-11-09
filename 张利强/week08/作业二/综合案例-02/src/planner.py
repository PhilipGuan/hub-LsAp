"""
问题规划模块

将研究主题拆解为可搜索的子问题
"""

import json
import re
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from loguru import logger


@dataclass
class SubQuestion:
    """子问题数据类"""
    id: str
    question: str
    keywords: List[str]
    priority: int  # 1-5，5为最高优先级
    description: str = ""
    related_questions: List[str] = None

    def __post_init__(self):
        if self.related_questions is None:
            self.related_questions = []


class ResearchPlanner:
    """研究规划器"""

    def __init__(self):
        self.planning_templates = {
            # 技术选型
            "技术选型": [
                {
                    "patterns": ["技术选型", "技术方案", "架构设计", "框架选择"],
                    "questions": [
                        "当前主流的技术方案有哪些？",
                        "各方案的优缺点对比是什么？",
                        "社区活跃度和成熟度如何？",
                        "学习成本和上手难度",
                        "性能和扩展性分析"
                    ]
                }
            ],
            # 行业分析
            "行业分析": [
                {
                    "patterns": ["行业分析", "市场趋势", "竞争格局", "发展前景"],
                    "questions": [
                        "市场规模和增长趋势",
                        "主要竞争对手分析",
                        "产业链结构",
                        "政策法规影响",
                        "技术创新方向"
                    ]
                }
            ],
            # 产品对比
            "产品对比": [
                {
                    "patterns": ["产品对比", "竞品分析", "功能对比", "优劣比较"],
                    "questions": [
                        "主要竞品有哪些？",
                        "核心功能对比",
                        "用户体验差异",
                        "定价策略分析",
                        "市场份额统计"
                    ]
                }
            ]
        }

    def plan_research(
        self,
        topic: str,
        domain: Optional[str] = None
    ) -> List[SubQuestion]:
        """
        规划研究子问题

        Args:
            topic: 研究主题
            domain: 研究领域（可选）

        Returns:
            子问题列表
        """
        logger.info(f"开始规划研究主题: {topic}")

        # 分析主题类型
        topic_type = self._analyze_topic_type(topic, domain)

        # 生成基础问题
        base_questions = self._generate_base_questions(topic, topic_type)

        # 生成延伸问题
        extended_questions = self._generate_extended_questions(topic, base_questions)

        # 合并并去重
        all_questions = base_questions + extended_questions
        unique_questions = self._deduplicate_questions(all_questions)

        # 优先级排序
        prioritized_questions = self._prioritize_questions(unique_questions)

        logger.info(f"规划完成，生成 {len(prioritized_questions)} 个子问题")
        return prioritized_questions

    def _analyze_topic_type(self, topic: str, domain: Optional[str]) -> str:
        """分析主题类型"""
        # 检查主题中的关键词
        topic_lower = topic.lower()

        if any(keyword in topic_lower for keyword in ["技术", "框架", "架构", "语言", "工具"]):
            return "技术选型"
        elif any(keyword in topic_lower for keyword in ["行业", "市场", "趋势", "竞争", "分析"]):
            return "行业分析"
        elif any(keyword in topic_lower for keyword in ["产品", "对比", "竞品", "功能", "价格"]):
            return "产品对比"
        elif domain:
            return domain
        else:
            return "综合分析"

    def _generate_base_questions(self, topic: str, topic_type: str) -> List[SubQuestion]:
        """生成基础问题"""
        questions = []

        # 根据主题类型使用模板
        if topic_type in self.planning_templates:
            template = self.planning_templates[topic_type][0]
            for i, question_template in enumerate(template["questions"], 1):
                question = self._format_question(question_template, topic)
                keywords = self._extract_keywords(question)
                sub_question = SubQuestion(
                    id=f"q{i:02d}",
                    question=question,
                    keywords=keywords,
                    priority=i  # 基础问题按顺序分配优先级
                )
                questions.append(sub_question)

        # 添加通用问题
        general_questions = [
            "关于 {topic} 的最新进展和发展趋势",
            "{topic} 的核心概念和基础知识",
            "关于 {topic} 的常见问题和解决方案"
        ]

        for i, template in enumerate(general_questions, len(questions) + 1):
            question = self._format_question(template, topic)
            keywords = self._extract_keywords(question)
            sub_question = SubQuestion(
                id=f"q{i:02d}",
                question=question,
                keywords=keywords,
                priority=3  # 通用问题中等优先级
            )
            questions.append(sub_question)

        return questions

    def _generate_extended_questions(
        self,
        topic: str,
        base_questions: List[SubQuestion]
    ) -> List[SubQuestion]:
        """生成延伸问题"""
        extended = []

        # 基于基础问题生成相关问题
        for base_q in base_questions:
            # 生成案例相关的问题
            case_question = f"关于 {base_q.question} 的实际案例和最佳实践"
            extended.append(SubQuestion(
                id=f"e_{base_q.id}",
                question=case_question,
                keywords=self._extract_keywords(case_question),
                priority=2,  # 延伸问题优先级较低
                related_questions=[base_q.id]
            ))

            # 生成争议或争议性的问题
            debate_question = f"关于 {base_q.question} 的不同观点和争议"
            extended.append(SubQuestion(
                id=f"d_{base_q.id}",
                question=debate_question,
                keywords=self._extract_keywords(debate_question),
                priority=4,  # 争议性问题优先级较高
                related_questions=[base_q.id]
            ))

        return extended

    def _format_question(self, template: str, topic: str) -> str:
        """格式化问题模板"""
        return template.replace("{topic}", topic)

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        # 移除标点符号
        text = re.sub(r'[^\w\s]', ' ', text)

        # 提取中文关键词（简单实现）
        chinese_words = re.findall(r'[一-鿿]+', text)

        # 提取英文关键词
        english_words = re.findall(r'[a-zA-Z]{3,}', text)

        # 合并并去重
        keywords = list(set(chinese_words + english_words))

        # 过滤掉太短的词
        keywords = [kw for kw in keywords if len(kw) > 2]

        return keywords[:5]  # 返回前5个关键词

    def _deduplicate_questions(self, questions: List[SubQuestion]) -> List[SubQuestion]:
        """去重相似问题"""
        unique = []
        seen_questions = set()

        for q in questions:
            # 简单去重（可以后续用更复杂的算法）
            if q.question not in seen_questions:
                seen_questions.add(q.question)
                unique.append(q)

        return unique

    def _prioritize_questions(self, questions: List[SubQuestion]) -> List[SubQuestion]:
        """优先级排序"""
        # 根据优先级和问题类型重新排序
        priority_order = {5: 0, 4: 1, 3: 2, 2: 3, 1: 4}

        questions.sort(key=lambda x: (priority_order[x.priority], x.id))

        return questions

    def save_plan(self, session_id: str, questions: List[SubQuestion]) -> None:
        """保存规划结果"""
        plan_data = {
            "session_id": session_id,
            "planned_at": str(int(time.time())),
            "questions": [asdict(q) for q in questions]
        }

        from pathlib import Path
        plan_file = Path("data/sessions") / f"{session_id}_plan.json"
        plan_file.parent.mkdir(parents=True, exist_ok=True)

        with open(plan_file, 'w', encoding='utf-8') as f:
            json.dump(plan_data, f, ensure_ascii=False, indent=2)

        logger.info(f"研究规划已保存: {plan_file}")

    def load_plan(self, session_id: str) -> Optional[List[SubQuestion]]:
        """加载规划结果"""
        try:
            from pathlib import Path
            plan_file = Path("data/sessions") / f"{session_id}_plan.json"

            if not plan_file.exists():
                return None

            with open(plan_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            questions = []
            for q_data in data["questions"]:
                questions.append(SubQuestion(
                    id=q_data["id"],
                    question=q_data["question"],
                    keywords=q_data["keywords"],
                    priority=q_data["priority"],
                    description=q_data.get("description", ""),
                    related_questions=q_data.get("related_questions", [])
                ))

            return questions
        except Exception as e:
            logger.error(f"加载规划失败: {str(e)}")
            return None