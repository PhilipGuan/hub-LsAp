import os
import asyncio

os.environ.setdefault("OPENAI_API_KEY", "sk-9a390d6f61334topabuc541ed77898bf")
os.environ.setdefault("OPENAI_BASE_URL", "https://api.deepseek.com")

from agents import Agent, Runner, AsyncOpenAI, OpenAIChatCompletionsModel
from agents.mcp import MCPServer
from agents.mcp.server import MCPServerSse
from agents import set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions")
set_tracing_disabled(True)


async def run(mcp_server: MCPServer):
    external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
    )

    agent = Agent(
        name="Assistant",
        instructions="你是一个助手，可以调用工具来回答用户的问题。",
        mcp_servers=[mcp_server],
        model=OpenAIChatCompletionsModel(
            model="deepseek-chat",
            openai_client=external_client,
        ),
    )

    # step1: mcp client 先 list tool，拿到 server 暴露的工具清单
    tools = await mcp_server.list_tools()
    print("MCP Server 暴露的工具：", [t.name for t in tools])

    # 分别测试新增的 3 个工具
    for message in [
        "北京现在的天气怎么样？",                    # get_weather
        "现在纽约（America/New_York）几点了？",       # get_current_time
        "帮我算一下 (3 + 4) * 2 等于多少？",          # calculate
    ]:
        print(f"\n>>> 用户：{message}")
        result = await Runner.run(starting_agent=agent, input=message)
        print(f"<<< 助手：{result.final_output}")


async def main():
    async with MCPServerSse(
        name="SSE Python Server",
        params={
            "url": "http://localhost:8000/sse",  # mcp server 通过 http(sse) 方式沟通
        },
    ) as server:
        await run(server)


if __name__ == "__main__":
    asyncio.run(main())
