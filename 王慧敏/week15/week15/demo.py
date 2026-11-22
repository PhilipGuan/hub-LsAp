"""命令行快速自测：无需启动 Web，直接跑两种实现。"""
import asyncio

from app import langgraph_impl, openai_agents_impl

CASES = [
    "今天项目顺利上线，太开心了！",
    "最近压力好大，感觉很沮丧。",
]


async def main():
    for impl_name, runner in [
        ("langgraph", langgraph_impl.run),
        ("openai-agents", openai_agents_impl.run),
    ]:
        print(f"\n===== {impl_name} =====")
        for text in CASES:
            r = await runner(text)
            print(f"输入: {text}")
            print(f"  情感: {r['sentiment']}")
            print(f"  回应: {r['response']}")


if __name__ == "__main__":
    asyncio.run(main())
