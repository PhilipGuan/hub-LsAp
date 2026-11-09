import os
# https://platform.deepseek.com/api_keys
os.environ["OPENAI_API_KEY"] = "0d4f15d1f5424topabu1010a3a47682a.SwboObfqjAU2qoSG"
os.environ["OPENAI_BASE_URL"] = "https://open.bigmodel.cn/api/paas/v4"

import asyncio
from agents import Agent, Runner, AsyncOpenAI, OpenAIChatCompletionsModel
from agents.mcp import MCPServer
from agents.mcp.server import MCPServerStreamableHttp
from agents.model_settings import ModelSettings

from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")
set_tracing_disabled(True)


async def run(mcp_server: MCPServer, message: str):
    external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
    )

    agent = Agent(
        name="Assistant",
        instructions=(
            "你是一个智能助手，能够调用外部工具完成任务。"
            "请优先调用相应的工具，而不是凭记忆回答。"
        ),
        mcp_servers=[mcp_server],
        model="GLM-4-Flash"
    )

    print(f"\n{'='*60}")
    print(f"Running: {message}")
    print('='*60)
    result = await Runner.run(starting_agent=agent, input=message)
    print(f"Answer: {result.final_output}")


    for item in result.new_items:
        if item.type == "tool_call_item":
            print("调用工具:", getattr(item.raw_item, "name", "未知"))

        elif item.type == "tool_call_output_item":
            print("工具返回:", item.output)
    
    return result.final_output


async def main():
    async with MCPServerStreamableHttp(
        name="SSE Python Server",
        params={
            "url": "http://localhost:8000/mcp",
        },
    ) as server:
        await run(server, "计算一下 (12 + 8) * 3 - 5 等于多少？")

        await run(server, "请对『今天天气真好，我们一起去公园散步吧』这句话进行分词")

        await run(server, "现在北京时间几点了？")

        await run(
            server,
            "现在北京时间几点？另外帮我掷 3 个骰子，然后计算这些骰子点数之和。"
        )

        await run(
            server,
            "请先获取当前时间，然后对『人工智能正在改变世界』进行分词，"
            "最后告诉我这两件事中你分别用了什么工具。"
        )


if __name__ == "__main__":
    asyncio.run(main())
