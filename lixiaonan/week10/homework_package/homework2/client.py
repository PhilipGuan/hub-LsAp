import os

# https://bailian.console.aliyun.com/?tab=model#/api-key
os.environ["OPENAI_API_KEY"] = "sk-ws-H.EIIXRYL.Me60.MEQCIGfSB1v26VNjkJ78lE1eumraa7SxvIos9mzqgvVHTrQeAiADTwLtIqkByXW3mDb49h8rW3SfaZ0FvGkdKgkOPuBKKw"
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

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
        mcp_servers=[mcp_server], # tools
        model=OpenAIChatCompletionsModel(
            model="qwen-max",
            openai_client=external_client,
        )
    )

    # list tool -> select tool -> execute tool -> return result -> gpt answer
    # list tool 得到 可选api 参数
    # select tool 本质就是function call

    # 给定 n 个非负整数表示每个宽度为 1 的柱子的高度图，计算按此排列的柱子，下雨之后能接多少雨水。
    message = "height = [0,1,0,2,1,0,1,3,2,1,2,1]"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)
    # 6

    # 等同于：工具选择、工具调用、工具回答的总结
    """
    step1，通过mcp client mcp list tool，得到mcp server 可选的工具、工具的参数、返回格式
    step2， 工具、工具的参数、返回格式 汇总为tools / functioncall 的格式 通过大模型 选择一个工具 -》 传入参数
    step3, 通过mcp client 调用execute tool 在mcp server 执行这个工具 -》 返回结果
    step4， 返回结果汇总在提示词，调用大模型得到结果
    """

    # 一个整数数组 nums，有一个大小为 k 的滑动窗口从数组的最左侧移动到数组的最右侧。你只可以看到在滑动窗口内的 k 个数字。滑动窗口每次只向右移动一位。
    message = "nums = [1,3,-1,-3,5,3,6,7], k = 3"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)
    # [3, 3, 5, 5, 6, 7]

    # 给你一个未排序的整数数组 nums ，请你找出其中没有出现的最小的正整数。
    message = "nums = [1, 2, 0]"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)
    # 3

async def main():
    async with MCPServerSse(
            name="SSE Python Server",
            params={
                "url": "http://localhost:8000/sse", # mcp server 通过http 方式沟通
            },
    )as server:
        await run(server)

if __name__ == "__main__":
    asyncio.run(main())
