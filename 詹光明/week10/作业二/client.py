import os

os.environ["OPENAI_API_KEY"] = "sk-2157e47c970d4topabud03aa43f4efe2f"
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"
MODEL_NAME = "deepseek-flash"

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

    # openai agent
    agent = Agent(
        name="Assistant",
        instructions="",
        mcp_servers=[mcp_server], # tools
        model=OpenAIChatCompletionsModel(
            model=MODEL_NAME,
            openai_client=external_client,
        )
    )

    # 人传入tool，function call execute tool
    message = "最近有什么新闻？"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)
    # 等同于：工具选择、工具调用、工具回答的总结

    """
    step1，通过mcp client mcp list tool，得到mcp server 可选的工具、工具的参数、返回格式
    step2， 工具、工具的参数、返回格式 汇总为 tools / function call 的格式 通过大模型 选择一个工具 -》 传入参数
    step3, 通过mcp client 调用execute tool 在mcp server 执行这个工具 -》 返回结果
    step4， 返回结果汇总在提示词，调用大模型得到结果
    """

    # tool
    message = "摇3次骰子得到结果。"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)

    questions = [
        "北京现在的天气怎么样？",  # 命中新增工具 get_weather
        "帮我算一下 (12 + 8) * 3 / 5 等于多少？",  # 命中新增工具 calculate
        "现在几点了？",  # 命中新增工具 get_current_time
    ]
    for question in questions:
        print("\n" + "=" * 60)
        print(f"用户提问：{question}")
        result = await Runner.run(starting_agent=agent, input=question)
        print(f"Agent 回答：{result.final_output}")

async def main():
    # 定义了远程 mcp server 链接方式
    async with MCPServerSse(
            name="SSE Python Server",
            params={
                "url": "http://localhost:8000/sse", # mcp server 通过http 方式沟通
            },
    ) as server:
        await run(server)

if __name__ == "__main__":
    asyncio.run(main())
