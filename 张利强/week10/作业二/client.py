import os

os.environ["OPENAI_API_KEY"] = "0d4f15d1f542482b85f1010a3a47682a.SwboObfqjAU2qoSG"
os.environ["OPENAI_BASE_URL"] = "https://open.bigmodel.cn/api/paas/v4"

from agents.mcp.server import MCPServerSse
import asyncio
from agents import Agent, Runner, AsyncOpenAI, OpenAIChatCompletionsModel
from agents.mcp import MCPServer

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
        # 把 MCP 服务器挂载到 Agent 上，Agent 就能自动获取该服务器上所有工具的定义
        mcp_servers=[mcp_server], # tools
        model=OpenAIChatCompletionsModel(
            model="GLM-4-Flash",
            openai_client=external_client,
        )
    )


    # 人传入tool，function call execute tool
    message = "最近有什么新闻？"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)


    # tool 11 17 13
    message = "摇3次骰子得到结果。"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)

    message = "创建一个日历事件，标题为'会议'，开始时间为'2026-10-01'，结束时间为'2026-10-03'。"
    result = await Runner.run(starting_agent=agent, input=message)
    print("创建的日历事件为：" + result.final_output)

    message = "发邮件，收件人为'test@example.com'，主题为'测试'，内容为'这是一封测试邮件'。"
    result = await Runner.run(starting_agent=agent, input=message)
    print("邮件发送结果为：" + result.final_output)

    message = "查询手机号'13633448064'的归属地和运营商。"
    result = await Runner.run(starting_agent=agent, input=message)
    print("手机号信息为：" + result.final_output)


async def main():
    # 创建一个 MCP 服务器，并通过 SSE 协议与之通信
    async with MCPServerSse(
            name="SSE Python Server",
            params={
                "url": "http://localhost:8900/sse", # mcp server 通过http 方式沟通
            },
    ) as server:
        await run(server)

if __name__ == "__main__":
    asyncio.run(main())
