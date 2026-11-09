import os

os.environ["OPENAI_API_KEY"] = "0d4f15d1f542482b85f1010a3a47682a.SwboObfqjAU2qoSG"
os.environ["OPENAI_BASE_URL"] = "https://open.bigmodel.cn/api/paas/v4"

import asyncio
from agents import Agent, Runner

from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")
set_tracing_disabled(True)

language_agent = Agent(
    name="language_agent",
    model="GLM-4-Flash",
    instructions="""
    你是一个语言专家，可以判断输入文本的语言类型(中文、日语、德语等等)。若是中文，翻译为英文；若不是中文，翻译成中文。
    输出为json格式，不需要markdown等其他信息。格式如下：
    {
    "query_input": "输入的文本",
    "language": "识别的语言类型，中文、日语、英文等",
    "result": "翻译结果"
    }
    """,
)

async def main():
    input_prompt = input("请输入需要翻译的文本，任何语言类型都可。")

    result = await Runner.run(
        language_agent,
        input_prompt
    )

    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())