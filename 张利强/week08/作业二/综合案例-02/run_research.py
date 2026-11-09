#!/usr/bin/env python3
"""
深度研究助手 - 启动脚本

封装了完整的研究流程，提供友好的命令行界面
"""

import asyncio
import sys
import os
from pathlib import Path

# 添加 src 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from main import ResearchAssistant


def print_usage():
    """打印使用说明"""
    print("""
深度研究助手 - 使用说明

基本用法：
    python run_research.py "研究主题"

示例：
    python run_research.py "人工智能的发展趋势"
    python run_research.py "微服务架构的优缺点" --max-rounds 4
    python run_research.py "新能源汽车市场分析" --max-pages 15

参数说明：
    --max-rounds INTEGER    最大检索轮数 (默认: 3)
    --max-pages INTEGER    每轮最大读取页面数 (默认: 10)
    --session-id STRING    自定义会话ID
    --bocha-key STRING     Bocha API Key
    --claude-key STRING    Claude API Key

环境变量：
    BOCHA_API_KEY          Bocha API Key（推荐方式）
    CLAUDE_API_KEY         Claude API Key（推荐方式）

更多信息请参考 README.md
""")


async def main():
    """主函数"""
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    # 解析参数
    topic = sys.argv[1]
    args = sys.argv[2:]

    # 解析可选参数
    max_rounds = 3
    max_pages = 10
    session_id = None
    bocha_key = None
    claude_key = None

    i = 0
    while i < len(args):
        if args[i] == "--max-rounds" and i + 1 < len(args):
            max_rounds = int(args[i + 1])
            i += 2
        elif args[i] == "--max-pages" and i + 1 < len(args):
            max_pages = int(args[i + 1])
            i += 2
        elif args[i] == "--session-id" and i + 1 < len(args):
            session_id = args[i + 1]
            i += 2
        elif args[i] == "--bocha-key" and i + 1 < len(args):
            bocha_key = args[i + 1]
            i += 2
        elif args[i] == "--claude-key" and i + 1 < len(args):
            claude_key = args[i + 1]
            i += 2
        else:
            print(f"未知参数: {args[i]}")
            sys.exit(1)

    # 从环境变量读取 API Key（如果未通过参数传入）
    if not bocha_key:
        bocha_key = os.getenv("BOCHA_API_KEY")
    if not claude_key:
        claude_key = os.getenv("CLAUDE_API_KEY")

    # 验证 API Key
    if not bocha_key and not claude_key:
        print("⚠️  警告：未设置任何 API Key")
        print("请设置环境变量或通过参数传入 API Key")
        print("  BOCHA_API_KEY 用于网络搜索")
        print("  CLAUDE_API_KEY 用于报告生成")
        sys.exit(1)

    # 创建研究助手
    api_keys = {}
    if bocha_key:
        api_keys["bocha"] = bocha_key
    if claude_key:
        api_keys["claude"] = claude_key

    assistant = ResearchAssistant(api_keys)

    # 显示配置信息
    print(f"🎯 研究主题: {topic}")
    print(f"📊 最大轮数: {max_rounds}")
    print(f"📄 每轮最大页数: {max_pages}")
    if session_id:
        print(f"🆔 会话ID: {session_id}")
    if bocha_key:
        print("🔍 搜索API: 已配置")
    if claude_key:
        print("📝 报告API: 已配置")
    print()

    try:
        # 运行研究
        report = await assistant.run_research(
            topic=topic,
            session_id=session_id,
            max_rounds=max_rounds,
            max_pages_per_round=max_pages
        )

        # 显示结果
        print("\n" + "="*50)
        print("📊 研究完成！")
        print("="*50)
        print(f"📋 报告标题: {report.title}")
        print(f"📈 置信度级别: {report.confidence_level}")
        print(f"📅 信息截止时间: {report.cutoff_date}")
        print(f"📊 结论数量: {len(report.conclusions)}")
        print(f"📋 遗留问题数量: {len(report.remaining_questions)}")
        print(f"🔗 来源数量: {len(report.sources)}")

        # 输出目录
        output_dir = Path("data/output") / (session_id or "default")
        print(f"\n💾 结果保存在: {output_dir}")

        # 显示部分结论
        print("\n🎯 关键结论（前5条）:")
        for i, conclusion in enumerate(report.conclusions[:5], 1):
            print(f"  {i}. {conclusion}")

        # 显示部分遗留问题
        print("\n❓ 遗留问题（前3条）:")
        for i, question in enumerate(report.remaining_questions[:3], 1):
            print(f"  {i}. {question}")

        print("\n📝 详细报告请查看:")
        print(f"  - JSON 格式: {output_dir}/research_report.json")
        print(f"  - Markdown 格式: {output_dir}/research_report.md")

    except KeyboardInterrupt:
        print("\n⚠️  研究被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 研究失败: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())