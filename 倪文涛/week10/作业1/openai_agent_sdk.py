import os
os.environ["OPENAI_API_KEY"] = "sk-xxx"
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

from agents import Agent, Runner
import asyncio

from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions") # 强制 SDK 使用 Chat Completions API，而非默认的 Responses API。
set_tracing_disabled(True) # 彻底关闭 SDK 的链路追踪（Tracing） 功能

other_to_chinese_agent = Agent(
    name="中文翻译专家",
    model="qwen3.7-flash-2026-07-15",
    instructions="你是一个中文翻译专家，请将非中文翻译为中文",
)


chinese_to_english_agent = Agent(
    name="英文翻译专家",
    model="qwen3.7-flash-2026-07-15",
    instructions="你是一个中文翻译英文专家，请将中文翻译为英文",
)

master_agent = Agent(
    name="语言专家",
    model="qwen3.7-flash-2026-07-15",
    instructions="""
        你是一个语言专家，判断用户输入的文本是什么语种，并根据语种选择对应工具进行翻译。
        任务全部完成后，你只能输出纯JSON，禁止任何解释、思考、分析文字，不能有markdown，不能有多余说明。
        JSON结构严格如下：
            {
                "query_input": 用户输入文本, 
                "language": 语种, 
                "result":翻译结果
            }
        """,
    tools= [
        other_to_chinese_agent.as_tool(
            tool_name="other_to_chinese_agent",
            tool_description="将非中文翻译为中文"
        ),
        chinese_to_english_agent.as_tool(
            tool_name="chinese_to_english_agent",
            tool_description="将中文翻译为英文"
        )
    ]
)

async def main():
    query = input("请输入一个文本，我帮你进行翻译：")
    result = await Runner.run(master_agent, query)
    print("\nagent最终输出:")
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())