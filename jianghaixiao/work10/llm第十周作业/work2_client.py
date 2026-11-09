"""
作业2 客户端：用 OpenAI Agent 连接 MCP Server，通过大模型调用上面 5 个工具。

参考代码：Week10/2-fastmcp使用/2-兼容OpenAI-Agent/client.py

运行前请先确保 work2_server.py 已经在另一个终端跑起来：
    python work2_server.py      # 终端 A，保持不关
    python work2_client.py      # 终端 B

关键点：新增工具只需要改 server，这里一行都不用改。
       因为工具清单是每次运行通过 MCP 协议自动拉取的（list_tools）。
"""
import os
import asyncio

# https://bailian.console.aliyun.com/?tab=model#/api-key
# 优先使用系统环境变量里的 key，没有才用下面这个兜底（交作业前建议删掉明文 key）
os.environ.setdefault("OPENAI_API_KEY", "sk-a581e3621c3c4f5d9429f8a2de31b860")
os.environ.setdefault("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

from agents import Agent, Runner, AsyncOpenAI, OpenAIChatCompletionsModel
from agents.mcp import MCPServer
from agents.mcp.server import MCPServerSse
from agents import set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions")   # qwen / deepseek 只支持 Chat Completions 协议
set_tracing_disabled(True)                   # 非官方端点必须关掉 tracing 上报

MCP_URL = "http://localhost:8000/sse"        # 必须和 server 的 transport="sse" 对应
MODEL_NAME = "qwen-max"

# 测试问题：覆盖 单工具 / 同一个工具调多次 / 一次提问触发多个工具
TEST_MESSAGES = [
    "最近有什么新闻？",                              # 原有工具 get_news
    "摇3次骰子得到结果。",                            # 原有工具 roll_dice
    "武汉未来3天的天气怎么样？",                       # 新工具 get_weather
    "北京和上海今天的天气，哪个更热？",                 # 新工具 get_weather 被调多次 + 比较
    "100 美元能换多少人民币？",                        # 新工具 convert_currency
    "(12+8)*3 等于多少？",                             # 新工具 calculate
    "武汉的天气怎么样？顺便算一下 25*4 是多少。",        # 组合：一次提问触发多个工具
]


async def run(mcp_server: MCPServer):
    external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
    )

    # 先打印一次服务器提供的工具清单，确认 5 个工具都被发现了
    tools = await mcp_server.list_tools()
    print(f"== MCP Server 提供了 {len(tools)} 个工具 ==")
    for t in tools:
        first_line = (t.description or "").strip().splitlines()[0] if t.description else ""
        print(f"  - {t.name:<18} {first_line}")
    print()

    agent = Agent(
        name="Assistant",
        instructions=(
            "你是一个助手，可以调用工具来回答用户问题。"
            "需要查天气、换算货币、做算术计算时，必须调用对应的工具，不要自己编造结果。"
        ),
        mcp_servers=[mcp_server],        # 只写这一行，工具清单由 MCP 协议自动发现
        model=OpenAIChatCompletionsModel(
            model=MODEL_NAME,
            openai_client=external_client,
        ),
    )

    for message in TEST_MESSAGES:
        print("=" * 66)
        print(f"用户：{message}")
        result = await Runner.run(starting_agent=agent, input=message)
        print(f"助手：{result.final_output}")


async def main():
    # 定义远程 mcp server 的连接方式：退出 with 时自动断开
    async with MCPServerSse(
            name="SSE Python Server",
            params={"url": MCP_URL},
    ) as server:
        await run(server)


if __name__ == "__main__":
    asyncio.run(main())