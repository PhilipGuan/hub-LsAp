"""
深度研究助手 - 主入口

提供命令行接口，执行完整的研究流程
"""

import asyncio
import argparse
import sys
from pathlib import Path
import json
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging

from planner import ResearchPlanner, SubQuestion
from searcher import SearchService, SearchResult
from reader import WebReader, PageContent
from judge import ContentJudge
from synthesizer import ReportSynthesizer, ResearchReport
from utils import (
    setup_logger,
    save_session,
    load_session,
    generate_session_id,
    create_output_dir,
    smart_retry
)


class ResearchAssistant:
    """深度研究助手主类"""

    def __init__(self, api_keys: Optional[Dict[str, str]] = None):
        # 设置日志
        setup_logger()
        self.logger = logging.getLogger("research-assistant")

        # 初始化组件
        self.planner = ResearchPlanner()
        self.judge = ContentJudge()

        # 从环境变量或参数获取 API keys
        self.search_service = SearchService(
            api_key=api_keys.get("bocha") if api_keys else None
        )
        self.synthesizer = ReportSynthesizer(
            api_key=api_keys.get("claude") if api_keys else None
        )

        self.reader = WebReader()

    async def run_research(
        self,
        topic: str,
        session_id: Optional[str] = None,
        max_rounds: int = 3,
        max_pages_per_round: int = 10
    ) -> ResearchReport:
        """
        运行完整的研究流程

        Args:
            topic: 研究主题
            session_id: 会话ID（可选）
            max_rounds: 最大检索轮数
            max_pages_per_round: 每轮最大读取页面数

        Returns:
            最终研究报告
        """
        if not session_id:
            session_id = generate_session_id()

        self.logger.info(f"开始研究主题: {topic}")
        self.logger.info(f"会话ID: {session_id}")

        # 尝试加载会话（如果已存在）
        session_data = load_session(session_id)
        if session_data:
            self.logger.info("检测到现有会话，从断点继续...")
            # 实现断点续存逻辑
            sub_questions, all_contents, all_search_results, current_round = self._resume_session(
                session_data, topic
            )
            self.logger.info(f"已恢复到第 {current_round} 轮")

        # 第一阶段：规划
        sub_questions = await self._plan_research(topic, session_id)

        # 收集所有内容
        all_contents = []
        all_search_results = []

        # 执行多轮检索
        start_round = 0
        if session_data and 'round' in session_data:
            start_round = session_data['round']
            self.logger.info(f"从第 {start_round} 轮继续")

        for round_num in range(start_round, max_rounds):
            self.logger.info(f"=== 第 {round_num + 1} 轮检索 ===")

            # 执行搜索
            round_contents = await self._execute_search_round(
                sub_questions,
                round_num,
                session_id
            )
            all_search_results.extend(round_contents)

            # 读取页面内容
            page_contents = await self._read_pages(
                round_contents,
                max_pages_per_round,
                session_id
            )
            all_contents.extend([self._content_to_dict(pc) for pc in page_contents])

            # 保存当前进度
            save_session(session_id, {
                "topic": topic,
                "round": round_num + 1,
                "sub_questions": [self._subquestion_to_dict(q) for q in sub_questions],
                "search_results": all_search_results,
                "contents": all_contents,
                "timestamp": str(int(datetime.now().timestamp()))
            })

            # 评估内容质量
            should_continue = await self._evaluate_and_decide(
                all_contents,
                session_id,
                round_num + 1,
                max_rounds
            )

            if not should_continue:
                self.logger.info("内容质量满足要求，停止检索")
                break

        # 第四阶段：综合生成
        self.logger.info("=== 开始综合生成报告 ===")
        report = await self._generate_final_report(
            topic,
            all_contents,
            sub_questions,
            session_id
        )

        # 保存最终结果
        self._save_results(report, session_id)

        return report

    async def _plan_research(
        self,
        topic: str,
        session_id: str
    ) -> List[SubQuestion]:
        """规划研究子问题"""
        self.logger.info("正在规划研究子问题...")

        sub_questions = self.planner.plan_research(topic)

        # 保存规划结果
        self.planner.save_plan(session_id, sub_questions)

        self.logger.info(f"规划完成，生成 {len(sub_questions)} 个子问题")
        for q in sub_questions:
            self.logger.info(f"- {q.id}: {q.question}")

        return sub_questions

    def _resume_session(
        self,
        session_data: Dict[str, Any],
        topic: str
    ) -> tuple[List[SubQuestion], List[Dict[str, Any]], List[Dict[str, Any]], int]:
        """
        从会话数据恢复研究状态

        Args:
            session_data: 保存的会话数据
            topic: 研究主题

        Returns:
            (sub_questions, all_contents, all_search_results, current_round)
        """
        # 恢复子问题
        sub_questions = []
        for q_data in session_data.get("sub_questions", []):
            from planner import SubQuestion
            sub_questions.append(SubQuestion(
                id=q_data["id"],
                question=q_data["question"],
                keywords=q_data["keywords"],
                priority=q_data["priority"],
                description=q_data.get("description", ""),
                related_questions=q_data.get("related_questions", [])
            ))

        # 恢复已收集的内容
        all_contents = session_data.get("contents", [])
        all_search_results = session_data.get("search_results", [])

        # 获取当前轮数
        current_round = session_data.get("round", 0)

        self.logger.info(f"恢复状态：")
        self.logger.info(f"- 子问题数量: {len(sub_questions)}")
        self.logger.info(f"- 已收集内容: {len(all_contents)}")
        self.logger.info(f"- 搜索结果: {len(all_search_results)}")
        self.logger.info(f"- 当前轮数: {current_round}")

        return sub_questions, all_contents, all_search_results, current_round

    async def _execute_search_round(
        self,
        sub_questions: List[SubQuestion],
        round_num: int,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """执行一轮搜索"""
        # 从子问题中提取查询词
        queries = []
        for q in sub_questions:
            if round_num == 0:
                # 第一轮使用原始问题
                queries.append(q.question)
            else:
                # 后续轮次使用关键词
                queries.extend(q.keywords)

        # 去重
        queries = list(set(queries))
        if round_num > 0:
            # 后续轮次，每个查询词生成2-3个变体
            expanded_queries = []
            for query in queries:
                variants = [
                    query,
                    f"{query} 最新",
                    f"{query} 2024",
                    f"{query} 分析"
                ]
                expanded_queries.extend(variants)
            queries = list(set(expanded_queries))[:10]  # 限制查询数量

        self.logger.info(f"本轮搜索查询词: {', '.join(queries[:5])}...")

        # 执行搜索
        search_results = []
        async with self.search_service as service:
            try:
                results = await service.batch_search(
                    queries,
                    count=5 if round_num == 0 else 3,  # 第一轮多搜，后续轮次精搜
                    summary=True
                )
                search_results = []
                for query, results_list in results.items():
                    for result in results_list:
                        if isinstance(result, SearchResult):
                            search_results.append({
                                "id": f"{hash(result.url)}",
                                "title": result.title,
                                "url": result.url,
                                "display_url": result.display_url,
                                "snippet": result.snippet,
                                "summary": result.summary,
                                "site_name": result.site_name,
                                "date_published": result.date_published,
                                "date_last_crawled": result.date_last_crawled,
                                "query": query,
                                "round": round_num + 1
                            })
            except Exception as e:
                self.logger.error(f"搜索失败: {str(e)}")

        self.logger.info(f"本轮搜索获得 {len(search_results)} 个结果")
        return search_results

    async def _read_pages(
        self,
        search_results: List[Dict[str, Any]],
        max_pages: int,
        session_id: str
    ) -> List[PageContent]:
        """读取页面内容"""
        # 过滤有效的URL
        valid_results = [
            r for r in search_results
            if r.get('url') and r.get('url').startswith(('http://', 'https://'))
        ]

        if not valid_results:
            self.logger.warning("没有有效的URL可以读取")
            return []

        # 选择读取的页面（优先选择高质量来源）
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
        async with self.reader as reader:
            try:
                contents = await reader.read_pages(selected_results)
                page_contents = contents
            except Exception as e:
                self.logger.error(f"页面读取失败: {str(e)}")

        return page_contents

    async def _evaluate_and_decide(
        self,
        contents: List[Dict[str, Any]],
        session_id: str,
        current_round: int,
        max_rounds: int
    ) -> bool:
        """评估内容质量并决定是否继续"""
        if not contents:
            self.logger.warning("没有内容可供评估")
            return True

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

    async def _generate_final_report(
        self,
        topic: str,
        contents: List[Dict[str, Any]],
        sub_questions: List[SubQuestion],
        session_id: str
    ) -> ResearchReport:
        """生成最终研究报告"""
        # 准备数据
        contents_dict = [self._content_to_dict(c) if hasattr(c, '__dict__') else c
                        for c in contents]
        questions_dict = [self._subquestion_to_dict(q) for q in sub_questions]

        # 生成报告
        report = await self.synthesizer.generate_report(
            topic,
            contents_dict,
            questions_dict,
            session_id
        )

        return report

    def _content_to_dict(self, content: PageContent) -> Dict[str, Any]:
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

    def _subquestion_to_dict(self, question: SubQuestion) -> Dict[str, Any]:
        """将SubQuestion转换为字典"""
        return {
            "id": question.id,
            "question": question.question,
            "keywords": question.keywords,
            "priority": question.priority,
            "description": question.description,
            "related_questions": question.related_questions
        }

    def _update_session_progress(self, session_id: str, data: Dict[str, Any]) -> None:
        """
        更新会话进度
        """
        session_data = load_session(session_id) or {}
        session_data.update(data)
        save_session(session_id, session_data)

    def _save_results(self, report: ResearchReport, session_id: str) -> None:
        """保存研究结果"""
        # 创建输出目录
        output_dir = create_output_dir(session_id)

        # 保存报告
        report_file = self.synthesizer.save_report(report, session_id)

        # 保存研究过程记录
        process_record = {
            "session_id": session_id,
            "topic": report.title.replace(" 研究报告", ""),
            "generated_at": report.metadata.get("generated_at"),
            "content_count": report.metadata.get("content_count"),
            "sub_questions_count": report.metadata.get("sub_questions_count"),
            "report_file": report_file,
            "confidence_level": report.confidence_level,
            "cutoff_date": report.cutoff_date
        }

        process_file = output_dir / "research_process.json"
        with open(process_file, 'w', encoding='utf-8') as f:
            json.dump(process_record, f, ensure_ascii=False, indent=2)

        self.logger.info(f"研究结果已保存到: {output_dir}")


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="深度研究助手")
    parser.add_argument("topic", help="研究主题")
    parser.add_argument("--session-id", help="会话ID（可选）")
    parser.add_argument("--max-rounds", type=int, default=3,
                       help="最大检索轮数（默认: 3）")
    parser.add_argument("--max-pages", type=int, default=10,
                       help="每轮最大读取页面数（默认: 10）")
    parser.add_argument("--bocha-key", help="Bocha API Key")
    parser.add_argument("--claude-key", help="Claude API Key")

    args = parser.parse_args()

    # 创建研究助手
    assistant = ResearchAssistant({
        "bocha": args.bocha_key,
        "claude": args.claude_key
    })

    # 运行研究
    try:
        report = asyncio.run(
            assistant.run_research(
                topic=args.topic,
                session_id=args.session_id,
                max_rounds=args.max_rounds,
                max_pages_per_round=args.max_pages
            )
        )

        print(f"\n📊 研究完成！报告已保存至: {report.metadata.get('session_id')}")

    except KeyboardInterrupt:
        print("\n⚠️  研究被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 研究失败: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()