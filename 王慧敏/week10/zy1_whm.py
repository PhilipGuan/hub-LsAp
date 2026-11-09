# 作业1: 按照下面 openai agents 框架，实现一个应用需求：用户输入文本，判断下文本的语种（是什么语言的）
# 然后如果是非中文的，则翻译为中文；如果是中文，则翻译为英文。最终输出格式需要为如下json：
# {
#     "query_input": "",
#     "language": "",
#     "result": ""
# }
import os 

os.environ["OPENAI_API_KEY"] = "sk-ws-H.ELXIRRX.cc3c.MEUCIG8aoUwJf9gMkdo31Ognz4CO75a4sP7_ScyZpxPgGJiiAiEAmgbE8qR-ktSbag7aU6zSmyxJqEWDgcAFLg_zfGwMWOE"
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
import asyncio

from pydantic import BaseModel

from agents import Agent, Runner, handoff

from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions") # 强制 SDK 使用 Chat Completions API，而非默认的 Responses API。
set_tracing_disabled(True) # 彻底关闭 SDK 的链路追踪（Tracing） 功能


# 1. 定义结构化输出，对应最终的 JSON 格式
class LanguageOutput(BaseModel):
    query_input: str   # 用户输入的原始文本
    language: str      # 文本的语种，例如 "中文" / "英文" / "日语" ...
    result: str        # 翻译结果：非中文 -> 中文；中文 -> 英文


# 2. 翻译智能体：负责最终翻译，并输出 LanguageOutput 结构
translator = Agent(
    name="翻译专家",
    model="qwen-max",
    instructions=(
        "你是翻译专家。\n"
        "1. 对话历史里包含：(a) 用户最初输入的原文（用户的第一条消息）；(b) 上一个智能体「语种检测专家」给出的语种。\n"
        "2. 若语种不是中文，翻译成中文；若是中文，翻译成英文。\n"
        "3. 输出 JSON，字段：query_input（用户最初输入的原文，原样保留）、"
        "language（检测出的语种，用中文名）、result（译文）。"
    ),
    output_type=LanguageOutput,
)


# 3. 语种检测智能体：判断语种后，通过 handoff 移交给翻译专家
detector = Agent(
    name="语种检测专家",
    model="qwen-max",
    instructions=(
        "你是语种检测专家。判断用户输入文本的语种，用中文命名（如“中文”“英文”“日语”“法语”等），"
        "然后立即移交给「翻译专家」处理。"
    ),
    handoffs=[handoff(translator, tool_name_override="transfer_to_translator")],
)


async def main():
    # 在一个事件循环里连续跑两次，避免 Windows 下多次 asyncio.run 报 “Event loop is closed”
    # 从 detector 开始，由它自动 handoff 到 translator，最终 final_output 是 LanguageOutput
    out1 = await Runner.run(detector, "Hello, how are you today?")
    print(out1.final_output.model_dump_json())

    out2 = await Runner.run(detector, "今天天气真好，我们去公园散步吧。")
    print(out2.final_output.model_dump_json())


if __name__ == "__main__":
    asyncio.run(main())