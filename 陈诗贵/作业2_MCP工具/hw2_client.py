# -*- coding: utf-8 -*-
"""作业2 MCP Client：Agent 通过 SSE 连接 MCP Server，调用新增的工具。

运行前请先启动 server：python hw2_server.py
"""
import os

# DeepSeek（OpenAI 兼容协议）
os.environ["OPENAI_API_KEY"] = ""
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

# 本机开着系统代理（如 Clash）时，localhost 请求会被代理拦截导致 502，
# 让本地 MCP Server 的连接绕过代理（不影响 DeepSeek API 走代理）
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import asyncio
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
        instructions="你可以使用 MCP 工具回答用户问题，需要时主动调用工具。",
        mcp_servers=[mcp_server],
        model=OpenAIChatCompletionsModel(
            model="deepseek-chat",
            openai_client=external_client,
        ),
    )

    # 分别触发 3 个新增工具 + 1 个组合调用
    messages = [
        "北京今天天气怎么样？",          # -> get_weather
        "帮我计算 (3+5)*2 等于多少？",    # -> calculate
        "现在几点了？",                  # -> get_current_time
        "先摇2次骰子，再告诉我上海的天气。",  # -> roll_dice + get_weather 组合
        "帮我算一下 10/0 等于多少？",      # 边界用例：除零
    ]
    for message in messages:
        print("=" * 60)
        print(f"Running: {message}")
        result = await Runner.run(starting_agent=agent, input=message)
        print(result.final_output)


async def main():
    async with MCPServerSse(
            name="SSE Python Server",
            params={
                "url": "http://localhost:8000/sse",
            },
    ) as server:
        await run(server)


if __name__ == "__main__":
    asyncio.run(main())
