import os
import asyncio
from openai import AsyncOpenAI
from agents import (
    Agent,
    Runner,
    OpenAIChatCompletionsModel,
    set_default_openai_api,
    set_tracing_disabled,
)
from agents.mcp import MCPServer
from agents.mcp.server import MCPServerSse

# ---------- DeepSeek configuration ----------
os.environ["OPENAI_API_KEY"] = "sk-bf4240833ba44d6ea4e6b33dacd6be11"  # replace with your real DeepSeek key
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

set_default_openai_api("chat_completions")
set_tracing_disabled(True)


async def run(mcp_server: MCPServer):
    external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
    )

    agent = Agent(
        name="Assistant",
        instructions="You are a helpful assistant. Use the available tools when needed.",
        mcp_servers=[mcp_server],
        model=OpenAIChatCompletionsModel(
            model="deepseek-flash",
            openai_client=external_client,
        ),
    )

    # Example 1
    message = "最近有什么新闻？"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)
    print("-" * 50)

    # Example 2
    message = "摇3次骰子得到结果。"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)

    # Example 3
    message = "求10和32的和。"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)

    # Example 4
    message = "你好，我是Tom"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)

    # Example 5
    message = "你来自哪个城市？"
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
