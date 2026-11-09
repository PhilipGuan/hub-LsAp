#!/usr/bin/env python3
"""
运行完整的研究演示
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# 添加 src 路径
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from searcher import SearchResult
from reader import WebReader, PageContent
from judge import ContentJudge
from planner import ResearchPlanner, SubQuestion
from synthesizer import ReportSynthesizer, ResearchReport
from utils import generate_session_id, create_output_dir, setup_logger
import asyncio

# 模拟搜索结果
def mock_search_results():
    return [
        {
            "id": "1",
            "title": "人工智能：从理论到实践",
            "url": "https://example.com/ai-theory",
            "display_url": "https://example.com/ai-theory",
            "snippet": "人工智能（AI）是计算机科学的一个分支，旨在创建能够执行通常需要人类智能的任务的系统。",
            "summary": "人工智能作为一门学科，其发展历程可以追溯到20世纪50年代。从最初的符号主义到现在的深度学习，AI技术经历了多次起伏。",
            "site_name": "科技前沿",
            "date_published": "2024-01-15",
            "date_last_crawled": "2024-01-15"
        },
        {
            "id": "2",
            "title": "2024年人工智能发展趋势报告",
            "url": "https://example.com/ai-trends-2024",
            "display_url": "https://example.com/ai-trends-2024",
            "snippet": "2024年，人工智能技术将迎来新的发展高潮，大模型、多模态AI等将成为主流。",
            "summary": "报告指出，2024年AI技术的发展主要集中在三个方向：大模型的持续优化、多模态技术的成熟应用、以及AI与传统行业的深度融合。",
            "site_name": "AI研究院",
            "date_published": "2024-03-20",
            "date_last_crawled": "2024-03-20"
        },
        {
            "id": "3",
            "title": "人工智能在各行业的应用案例",
            "url": "https://example.com/ai-applications",
            "display_url": "https://example.com/ai-applications",
            "snippet": "人工智能已经广泛应用于医疗、金融、教育、制造等多个行业，带来了显著的效率提升。",
            "summary": "在医疗领域，AI辅助诊断系统准确率达到95%；在金融领域，风控模型效率提升50%；在教育领域，个性化学习平台受益学生超过100万。",
            "site_name": "行业应用",
            "date_published": "2024-02-10",
            "date_last_crawled": "2024-02-10"
        }
    ]

# 模拟页面内容
def mock_page_contents():
    return [
        PageContent(
            url="https://example.com/ai-theory",
            title="人工智能：从理论到实践",
            content="""
人工智能（AI）是计算机科学的一个分支，旨在创建能够执行通常需要人类智能的任务的系统。
这些任务包括学习、推理、问题解决、感知和语言理解等。

人工智能的发展经历了多个阶段：
1. 1950-1960年代：符号主义AI，基于逻辑推理
2. 1970-1980年代：知识工程，依赖专家系统
3. 1980-1990年代：机器学习兴起
4. 2000年代至今：深度学习爆发，特别是2012年后的大模型时代

核心技术包括：
- 机器学习：让计算机从数据中学习
- 深度学习：基于神经网络的机器学习
- 自然语言处理：让计算机理解和生成人类语言
- 计算机视觉：让计算机理解和分析图像
- 机器人技术：结合硬件和软件的智能系统
            """,
            extracted_at=datetime.now().timestamp()
        ),
        PageContent(
            url="https://example.com/ai-trends-2024",
            title="2024年人工智能发展趋势报告",
            content="""
2024年人工智能技术发展趋势：

1. 大模型持续优化
   - 参数规模达到万亿级别
   - 训练效率提升40%
   - 推理速度提升3倍
   - 多语言支持更加完善

2. 多模态AI成熟
   - 文本、图像、音频、视频融合理解
   - 跨模态生成能力增强
   - 实时交互体验改善

3. 行业深度融合
   - 医疗：AI辅助诊断普及
   - 金融：智能风控标准化
   - 教育：个性化教学规模化
   - 制造：智能制造普及化

4. 技术挑战与机遇
   - 数据安全与隐私保护
   - 算法公平性与透明度
   - 能源消耗优化
   - 人才培养与储备
            """,
            extracted_at=datetime.now().timestamp()
        ),
        PageContent(
            url="https://example.com/ai-applications",
            title="人工智能在各行业的应用案例",
            content="""
人工智能在各行业的成功应用：

医疗健康领域：
- 辅助诊断：CT影像识别准确率95%
- 药物研发：新药研发周期缩短50%
- 个性化治疗：治疗方案优化60%

金融服务：
- 智能风控：风险识别准确率提升40%
- 欺诈检测：识别速度提升100倍
- 量化交易：策略优化效果显著

教育培训：
- 个性化学习：学习效率提升35%
- 智能批改：作业批改时间减少80%
- 语言学习：口语练习AI辅导

智能制造：
- 预测性维护：设备故障率降低60%
- 质量控制：缺陷检测准确率99%
- 供应链优化：物流成本降低25%

交通运输：
- 自动驾驶：L4级技术成熟
- 交通管理：拥堵减少30%
- 物流配送：效率提升40%
            """,
            extracted_at=datetime.now().timestamp()
        )
    ]

async def run_full_research_demo():
    """运行完整的研究演示"""
    # 设置日志
    setup_logger()

    # 生成会话ID
    session_id = generate_session_id()

    print("🔍 深度研究助手 - 完整演示")
    print("=" * 60)
    print(f"研究主题：人工智能的发展趋势")
    print(f"会话ID：{session_id}")
    print()

    # 第一步：问题规划
    print("🧠 第一步：问题规划")
    planner = ResearchPlanner()
    sub_questions = planner.plan_research("人工智能的发展趋势")
    print(f"生成 {len(sub_questions)} 个子问题")
    for q in sub_questions[:3]:
        print(f"  - {q.id}: {q.question}")
    print()

    # 第二步：搜索（使用模拟数据）
    print("🔍 第二步：信息检索")
    search_results = mock_search_results()
    print(f"找到 {len(search_results)} 个相关结果")
    for result in search_results:
        print(f"  - {result['title']} ({result['site_name']})")
    print()

    # 第三步：内容阅读
    print("📖 第三步：内容阅读")
    page_contents = mock_page_contents()
    print(f"读取 {len(page_contents)} 个页面内容")
    print()

    # 第四步：质量评估
    print("📊 第四步：质量评估")
    judge = ContentJudge()
    qualities = judge.evaluate_batch(search_results)
    avg_score = sum(q.score for q in qualities) / len(qualities)
    print(f"平均质量分数：{avg_score:.1f}/100")
    print(f"质量评级：{qualities[0].reliability}")
    print()

    # 第五步：报告生成（使用模拟报告）
    print("📝 第五步：生成研究报告")

    # 创建模拟报告
    mock_report = ResearchReport(
        title="人工智能的发展趋势研究报告",
        abstract="本研究分析了人工智能技术的最新发展趋势，涵盖大语言模型、多模态AI、行业应用等核心领域。研究表明，AI技术正在从单一功能向综合智能方向发展，应用场景不断拓展，同时监管和伦理问题日益凸显。",
        sections=[
            {
                "title": "大语言模型的最新进展",
                "content": """大语言模型（LLM）继续呈现规模化发展趋势。2024年，主流模型参数规模突破万亿级别，训练效率显著提升。同时，模型能力不断增强，在推理、代码生成、创意写作等领域展现出接近人类的专业水平。

关键进展包括：
- 模型训练成本降低 40%
- 推理速度提升 3 倍
- 多语言支持能力增强"""
            },
            {
                "title": "多模态AI的崛起",
                "content": """多模态AI成为新的技术热点，能够同时处理文本、图像、音频、视频等多种信息形式。这种技术极大地拓展了AI的应用边界。

主要应用场景：
- 智能内容创作
- 跨媒体信息检索
- 人机交互界面升级"""
            },
            {
                "title": "AI在各行业的应用现状",
                "content": """AI技术在各行各业的应用呈现差异化发展态势：

1. **医疗健康**：诊断准确率提升至95%，药物研发周期缩短50%
2. **金融服务**：风险评估精度提升30%，自动化交易占比达60%
3. **制造业**：预测性维护减少停机时间45%，质量控制效率提升50%"""
            }
        ],
        conclusions=[
            "AI技术正从专用智能向通用智能过渡，多模态成为新常态",
            "AI与传统行业的融合不断深入，创造新价值",
            "各国加快AI立法进程，平衡创新与安全"
        ],
        remaining_questions=[
            "AI能否实现真正的通用人工智能（AGI）？",
            "如何平衡AI发展与就业影响？",
            "自主系统的责任边界如何界定？"
        ],
        sources=[
            {
                "title": "人工智能：从理论到实践",
                "url": "https://example.com/ai-theory",
                "site_name": "科技前沿",
                "date_published": "2024-01-15",
                "relevance_score": 85
            },
            {
                "title": "2024年人工智能发展趋势报告",
                "url": "https://example.com/ai-trends-2024",
                "site_name": "AI研究院",
                "date_published": "2024-03-20",
                "relevance_score": 90
            }
        ],
        confidence_level="高",
        cutoff_date="2024年3月20日",
        metadata={
            "session_id": session_id,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_count": len(page_contents),
            "sub_questions_count": len(sub_questions)
        }
    )

    print("✅ 报告生成完成")
    print(f"报告标题：{mock_report.title}")
    print(f"置信度：{mock_report.confidence_level}")
    print(f"结论数量：{len(mock_report.conclusions)}")
    print()

    # 保存结果
    print("💾 保存研究结果")
    output_dir = create_output_dir(session_id)

    # 保存报告
    import json
    report_file = output_dir / "research_report.json"
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump({
            "title": mock_report.title,
            "abstract": mock_report.abstract,
            "sections": mock_report.sections,
            "conclusions": mock_report.conclusions,
            "remaining_questions": mock_report.remaining_questions,
            "sources": mock_report.sources,
            "confidence_level": mock_report.confidence_level,
            "cutoff_date": mock_report.cutoff_date,
            "metadata": mock_report.metadata
        }, f, ensure_ascii=False, indent=2)

    # 保存过程记录
    process_file = output_dir / "research_process.json"
    process_data = {
        "session_id": session_id,
        "topic": "人工智能的发展趋势",
        "planned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rounds_completed": 1,
        "content_count": len(page_contents),
        "sub_questions_count": len(sub_questions),
        "avg_score": avg_score,
        "report_file": str(report_file),
        "confidence_level": mock_report.confidence_level,
        "cutoff_date": mock_report.cutoff_date
    }
    with open(process_file, 'w', encoding='utf-8') as f:
        json.dump(process_data, f, ensure_ascii=False, indent=2)

    print(f"📄 结果保存在：{output_dir}")
    print("\n🎉 研究完成！")
    print("=" * 60)

    # 显示报告摘要
    print("\n📋 报告摘要：")
    print(mock_report.abstract)
    print("\n🎯 关键结论：")
    for i, conclusion in enumerate(mock_report.conclusions, 1):
        print(f"  {i}. {conclusion}")

if __name__ == "__main__":
    asyncio.run(run_full_research_demo())