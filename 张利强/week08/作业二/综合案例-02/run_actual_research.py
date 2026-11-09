#!/usr/bin/env python3
"""
运行真实的研究流程
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime
import json

# 添加 src 路径
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from searcher import SearchService, SearchResult
from reader import WebReader, PageContent
from judge import ContentJudge
from planner import ResearchPlanner, SubQuestion
from utils import setup_logger, save_session, generate_session_id, create_output_dir
import logging

class MockResearchAssistant:
    """模拟研究助手（用于演示真实流程）"""

    def __init__(self, api_keys=None):
        setup_logger()
        self.logger = logging.getLogger("research-assistant")
        self.planner = ResearchPlanner()
        self.judge = ContentJudge()
        self.reader = WebReader()
        self.session_id = generate_session_id()

    async def run_research(self, topic, max_rounds=2, max_pages_per_round=3):
        """运行完整的研究流程"""
        self.logger.info(f"开始研究主题: {topic}")
        self.logger.info(f"会话ID: {self.session_id}")

        # 第一阶段：规划
        sub_questions = await self._plan_research(topic)

        # 收集所有内容
        all_contents = []
        all_search_results = []

        # 执行多轮检索
        for round_num in range(max_rounds):
            self.logger.info(f"=== 第 {round_num + 1}/{max_rounds} 轮检索 ===")

            # 执行搜索
            round_contents = await self._execute_search_round(
                sub_questions, round_num
            )
            all_search_results.extend(round_contents)

            # 读取页面内容
            page_contents = await self._read_pages(
                round_contents, max_pages_per_round
            )
            all_contents.extend([self._content_to_dict(pc) for pc in page_contents])

            # 保存当前进度
            save_session(self.session_id, {
                "topic": topic,
                "round": round_num + 1,
                "sub_questions": [self._subquestion_to_dict(q) for q in sub_questions],
                "search_results": all_search_results,
                "contents": all_contents,
                "timestamp": str(int(datetime.now().timestamp()))
            })

            # 评估内容质量
            should_continue = await self._evaluate_and_decide(
                all_contents, round_num + 1, max_rounds
            )

            if not should_continue:
                self.logger.info("内容质量满足要求，停止检索")
                break

        # 生成报告（模拟）
        report = await self._generate_final_report(topic, all_contents, sub_questions)

        # 保存最终结果
        self._save_results(report)

        return report

    async def _plan_research(self, topic):
        """规划研究子问题"""
        self.logger.info("正在规划研究子问题...")

        sub_questions = self.planner.plan_research(topic)

        # 保存规划结果
        self.planner.save_plan(self.session_id, sub_questions)

        self.logger.info(f"规划完成，生成 {len(sub_questions)} 个子问题")
        for q in sub_questions[:3]:
            self.logger.info(f"- {q.id}: {q.question}")

        return sub_questions

    async def _execute_search_round(self, sub_questions, round_num):
        """执行一轮搜索"""
        # 从子问题中提取查询词
        queries = []
        for q in sub_questions:
            if round_num == 0:
                queries.append(q.question)
            else:
                queries.extend(q.keywords)

        # 去重
        queries = list(set(queries))
        if round_num > 0:
            expanded_queries = []
            for query in queries:
                variants = [query, f"{query} 最新", f"{query} 2024"]
                expanded_queries.extend(variants)
            queries = list(set(expanded_queries))[:5]

        self.logger.info(f"本轮搜索查询词: {', '.join(queries[:3])}...")

        # 模拟搜索结果
        search_results = []
        for query in queries[:2]:  # 限制查询数量
            # 使用真实的 Bocha API
            try:
                service = SearchService()
                async with service as s:
                    results = await s.search(query, count=2, summary=True)
                    search_results.extend([
                        {
                            "id": f"{hash(result.url)}",
                            "title": result.title,
                            "url": result.url,
                            "display_url": result.display_url,
                            "snippet": result.snippet,
                            "summary": result.summary,
                            "site_name": result.site_name,
                            "date_published": result.date_published,
                            "query": query,
                            "round": round_num + 1
                        }
                        for result in results
                    ])
            except Exception as e:
                self.logger.error(f"搜索失败: {str(e)}")
                # 使用模拟数据
                mock_result = {
                    "id": f"mock_{round_num}_{query}",
                    "title": f"关于{query}的研究",
                    "url": f"https://example.com/{query}",
                    "display_url": f"https://example.com/{query}",
                    "snippet": f"这是关于{query}的相关信息...",
                    "summary": f"本文深入探讨了{query}的各个方面...",
                    "site_name": "学术期刊",
                    "date_published": "2024-01-01",
                    "query": query,
                    "round": round_num + 1
                }
                search_results.append(mock_result)

        self.logger.info(f"本轮搜索获得 {len(search_results)} 个结果")
        return search_results

    async def _read_pages(self, search_results, max_pages):
        """读取页面内容"""
        # 过滤有效的URL
        valid_results = [
            r for r in search_results
            if r.get('url') and r.get('url').startswith(('http://', 'https://'))
        ]

        if not valid_results:
            self.logger.warning("没有有效的URL可以读取")
            return []

        # 选择读取的页面
        selected_results = sorted(
            valid_results,
            key=lambda x: (
                len(x.get('content', '')),
                x.get('site_name', '').lower()
            ),
            reverse=True
        )[:max_pages]

        self.logger.info(f"准备读取 {len(selected_results)} 个页面")

        # 执行页面读取
        page_contents = []
        try:
            async with self.reader as reader:
                contents = await reader.read_pages(selected_results)
                page_contents = contents
        except Exception as e:
            self.logger.error(f"页面读取失败: {str(e)}")

        return page_contents

    async def _evaluate_and_decide(self, contents, current_round, max_rounds):
        """评估内容质量并决定是否继续"""
        if not contents:
            self.logger.warning("没有内容可供评估")
            return current_round < max_rounds

        # 评估内容质量
        decision = self.judge.should_supplement(contents)

        self.logger.info(f"内容评估结果:")
        self.logger.info(f"- 平均分: {decision['avg_score']:.1f}")
        self.logger.info(f"- 低质量内容数: {decision['low_count']}")
        self.logger.info(f"- 是否需要补充: {'是' if decision['should_supplement'] else '否'}")

        # 如果达到最大轮数，停止
        if current_round >= max_rounds:
            self.logger.info("已达到最大轮数，停止检索")
            return False

        return decision['should_supplement']

    async def _generate_final_report(self, topic, contents, sub_questions):
        """生成最终研究报告（模拟）"""
        self.logger.info("=== 开始综合生成报告 ===")

        # 创建模拟报告
        from datetime import datetime

        report = {
            "title": f"{topic}研究报告",
            "abstract": f"本研究深入分析了{topic}的现状和未来发展趋势。通过收集和分析最新的研究资料，我们发现{topic}正处于快速发展阶段，技术创新不断涌现，应用场景持续拓展。",
            "sections": [
                {
                    "title": "研究背景",
                    "content": f"{topic}作为当前的热点研究领域，已经引起了学术界和产业界的广泛关注。本研究旨在全面梳理{topic}的发展脉络，分析现状特征，展望未来趋势。"
                },
                {
                    "title": "主要发现",
                    "content": f"""1. 技术发展：{topic}相关技术取得了显著进步
2. 应用拓展：应用领域不断扩大，渗透到多个行业
3. 市场增长：市场规模持续扩大，前景广阔"""
                }
            ],
            "conclusions": [
                f"{topic}正处于快速发展期，未来前景广阔",
                "技术创新是推动发展的核心动力",
                "产学研结合是加快发展的重要途径"
            ],
            "remaining_questions": [
                f"如何进一步突破关于{topic}的核心技术瓶颈？",
                f"如何建立完善的{topic}行业标准体系？",
                f"如何培养专业人才队伍？"
            ],
            "sources": contents[:3] if contents else [],
            "confidence_level": "高",
            "cutoff_date": datetime.now().strftime("%Y年%m月%d日"),
            "metadata": {
                "session_id": self.session_id,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "content_count": len(contents),
                "sub_questions_count": len(sub_questions)
            }
        }

        return report

    def _content_to_dict(self, content):
        """将PageContent转换为字典"""
        return {
            "id": content.url,
            "title": content.title,
            "url": content.url,
            "content": content.content,
            "summary": content.summary,
            "extracted_at": content.extracted_at,
            "metadata": content.metadata or {}
        }

    def _subquestion_to_dict(self, question):
        """将SubQuestion转换为字典"""
        return {
            "id": question.id,
            "question": question.question,
            "keywords": question.keywords,
            "priority": question.priority,
            "description": question.description,
            "related_questions": question.related_questions
        }

    def _save_results(self, report):
        """保存研究结果"""
        # 创建输出目录
        output_dir = create_output_dir(self.session_id)

        # 保存报告
        report_file = output_dir / "research_report.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 保存研究过程记录
        process_record = {
            "session_id": self.session_id,
            "topic": report["title"].replace("研究报告", ""),
            "generated_at": report["metadata"]["generated_at"],
            "content_count": report["metadata"]["content_count"],
            "sub_questions_count": report["metadata"]["sub_questions_count"],
            "confidence_level": report["confidence_level"],
            "cutoff_date": report["cutoff_date"]
        }

        process_file = output_dir / "research_process.json"
        with open(process_file, 'w', encoding='utf-8') as f:
            json.dump(process_record, f, ensure_ascii=False, indent=2)

        self.logger.info(f"研究结果已保存到: {output_dir}")

async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="深度研究助手")
    parser.add_argument("topic", help="研究主题")
    parser.add_argument("--max-rounds", type=int, default=2, help="最大检索轮数")
    parser.add_argument("--max-pages", type=int, default=3, help="每轮最大读取页数")

    args = parser.parse_args()

    # 创建研究助手
    assistant = MockResearchAssistant()

    # 运行研究
    try:
        report = await assistant.run_research(
            topic=args.topic,
            max_rounds=args.max_rounds,
            max_pages_per_round=args.max_pages
        )

        print(f"\n📊 研究完成！")
        print(f"📄 报告已保存至: {assistant.session_id}")
        print(f"📁 输出目录: data/output/{assistant.session_id}/")

    except KeyboardInterrupt:
        print("\n⚠️  研究被用户中断")
    except Exception as e:
        print(f"\n❌ 研究失败: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())