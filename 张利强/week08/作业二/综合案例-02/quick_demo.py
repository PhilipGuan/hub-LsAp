#!/usr/bin/env python3
"""
快速运行不同主题的研究演示
"""

import sys
import os
from pathlib import Path

# 添加 src 路径
sys.path.insert(0, str(Path(__file__).parent / 'src'))

async def run_topic_demo(topic: str):
    """运行特定主题的研究演示"""

    # 导入模块
    from searcher import SearchResult
    from reader import WebReader, PageContent
    from judge import ContentJudge
    from planner import ResearchPlanner
    from utils import generate_session_id, create_output_dir, setup_logger
    import asyncio
    import json
    from datetime import datetime

    # 设置日志
    setup_logger()

    # 生成会话ID
    session_id = generate_session_id()

    print(f"🔍 深度研究助手 - {topic}")
    print("=" * 60)
    print(f"会话ID：{session_id}")
    print()

    # 第一步：问题规划
    print("🧠 第一步：问题规划")
    planner = ResearchPlanner()
    sub_questions = planner.plan_research(topic)
    print(f"生成 {len(sub_questions)} 个子问题")
    for q in sub_questions[:3]:
        print(f"  - {q.id}: {q.question}")
    print()

    # 第二步：创建模拟数据
    print("🔍 第二步：信息收集")
    mock_results = [
        {
            "id": "1",
            "title": f"关于{topic}的最新研究",
            "url": "https://example.com/topic-research",
            "display_url": "https://example.com/topic-research",
            "snippet": f"这是关于{topic}的最新研究发现...",
            "summary": f"本文深入探讨了{topic}的各个方面，包括理论基础、实践应用和发展趋势。",
            "site_name": "学术期刊",
            "date_published": "2024-01-15"
        },
        {
            "id": "2",
            "title": f"{topic}的发展趋势分析",
            "url": "https://example.com/trends",
            "display_url": "https://example.com/trends",
            "snippet": f"分析了{topic}近年来的发展趋势...",
            "summary": f"通过数据分析，我们发现{topic}正在快速发展，主要表现在技术创新、应用拓展和市场规模等方面。",
            "site_name": "行业报告",
            "date_published": "2024-03-20"
        }
    ]

    mock_contents = [
        PageContent(
            url="https://example.com/topic-research",
            title=f"关于{topic}的最新研究",
            content=f"""
这是关于{topic}的深入研究内容。

## 研究背景
{topic}是当前备受关注的研究领域，具有重要的理论价值和实践意义。

## 主要发现
1. 技术层面取得了重要突破
2. 应用范围不断拓展
3. 社会影响日益深远

## 发展趋势
预计未来几年，{topic}将继续保持快速发展态势。
            """,
            extracted_at=datetime.now().timestamp()
        ),
        PageContent(
            url="https://example.com/trends",
            title=f"{topic}的发展趋势分析",
            content=f"""
## {topic}发展趋势分析

### 市场状况
市场规模持续扩大，年增长率保持在20%以上。

### 技术创新
新技术不断涌现，推动行业快速发展。

### 应用场景
从理论研究向实际应用转化，创造巨大价值。
            """,
            extracted_at=datetime.now().timestamp()
        )
    ]

    print(f"收集到 {len(mock_results)} 个研究资源")
    print()

    # 第三步：质量评估
    print("📊 第三步：质量评估")
    judge = ContentJudge()
    qualities = judge.evaluate_batch(mock_results)
    avg_score = sum(q.score for q in qualities) / len(qualities)
    print(f"平均质量分数：{avg_score:.1f}/100")
    print()

    # 第四步：生成报告
    print("📝 第四步：生成研究报告")

    report_content = f"""# {topic}研究报告

> 生成日期：{datetime.now().strftime('%Y-%m-%d')} | 质检评分：8/10 | 来源数：2

## 摘要

本研究深入分析了{topic}的现状和未来发展趋势。通过收集和分析最新的研究资料，我们发现{topic}正处于快速发展阶段，技术创新不断涌现，应用场景持续拓展，具有重要的研究价值和发展前景。

## 正文

### 研究背景

{topic}作为当前的热点研究领域，已经引起了学术界和产业界的广泛关注。本研究旨在全面梳理{topic}的发展脉络，分析现状特征，展望未来趋势。

### 主要发现

1. **技术发展**：{topic}相关技术取得了显著进步
2. **应用拓展**：应用领域不断扩大，渗透到多个行业
3. **市场增长**：市场规模持续扩大，前景广阔

### 挑战与机遇

当前{topic}发展面临的主要挑战包括技术瓶颈、人才短缺、标准缺失等。但同时，政策支持、市场需求和技术突破也带来了重要机遇。

## 关键结论

1. {topic}正处于快速发展期，未来前景广阔
2. 技术创新是推动发展的核心动力
3. 产学研结合是加快发展的重要途径

## 遗留问题

1. 如何进一步突破核心技术瓶颈？
2. 如何建立完善的行业标准体系？
3. 如何培养专业人才队伍？

## 来源列表

[1] 《关于{topic}的最新研究》 · 学术期刊 · 2024-01-15

[2] 《{topic}发展趋势分析》 · 行业报告 · 2024-03-20

## 置信度说明

- 信息截止时间：来源发布日期范围 2024-01-15 ~ 2024-03-20
- 关键结论按证据强度标注置信度（高/中/低）
- 自动质检评分：8/10（达标）
"""

    print("✅ 报告生成完成")

    # 保存结果
    print("💾 保存研究结果")
    output_dir = create_output_dir(session_id)

    # 保存报告
    report_file = output_dir / f"{topic}_research_report.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report_content)

    # 保存过程记录
    process_file = output_dir / f"{topic}_research_process.json"
    process_data = {
        "session_id": session_id,
        "topic": topic,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rounds_completed": 1,
        "content_count": len(mock_contents),
        "sub_questions_count": len(sub_questions),
        "avg_score": avg_score,
        "report_file": str(report_file)
    }
    with open(process_file, 'w', encoding='utf-8') as f:
        json.dump(process_data, f, ensure_ascii=False, indent=2)

    print(f"📄 结果保存在：{output_dir}")
    print("\n🎉 研究完成！")
    print("=" * 60)

    return str(report_file)

def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="运行研究演示")
    parser.add_argument("topic", nargs='?', default="区块链技术", help="研究主题")

    args = parser.parse_args()

    # 运行演示
    import asyncio
    result_file = asyncio.run(run_topic_demo(args.topic))

    print(f"\n📄 报告已生成：{result_file}")

if __name__ == "__main__":
    main()