import os

# https://bailian.console.aliyun.com/?tab=model#/api-key
os.environ["OPENAI_API_KEY"] = ""
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

import asyncio
import uuid

from openai.types.responses import ResponseContentPartDoneEvent, ResponseTextDeltaEvent
from agents import Agent, RawResponsesStreamEvent, Runner, TResponseInputItem, trace
from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")
set_tracing_disabled(True)

# 语言判断agent，返回是否是中文，是中文，则返回给中文翻译agent，不是中文则返回给非中文翻译agent
# 非中文翻译agent
# 中文翻译agent

chinese_agent = Agent(
    name="chinese_agent",
    model="qwen-max",
    instructions="""
        你是中文翻译专家，精通将中文翻译为英文。
        规则：
        - 准确、流畅、自然，避免翻译腔；
        - 保持原文语气、风格、格式；
        - 术语和专有名词必须准确；
        - 只输出译文，不加解释。
        若原文有歧义，在译文后用括号标注。
    """
)

english_agent = Agent(
    name="english_agent",
    model="qwen-max",
    instructions="""
        你是英文翻译专家，精通将英文翻译为中文。
        规则：
        - 准确、流畅、自然，避免翻译腔；
        - 保持原文语气、风格、格式；
        - 术语和专有名词必须准确；
        - 只输出译文，不加解释。
        若原文有歧义，在译文后用括号标注。
    """
)


# triage 定义的的名字 默认的功能用户提问 指派其他agent进行完成
triage_agent = Agent(
    name="triage_agent",
    model="qwen-max",
    instructions="Handoff to the appropriate agent based on the language of the request.",
    handoffs=[chinese_agent, english_agent],
)


async def main():
    # We'll create an ID for this conversation, so we can link each trace
    conversation_id = str(uuid.uuid4().hex[:16])

    msg = input("你好，我是一个翻译专家，精通中文与英文，请输入你要翻译的内容：")
    agent = triage_agent
    inputs: list[TResponseInputItem] = [{"content": msg, "role": "user"}]

    while True:
        with trace("Routing example", group_id=conversation_id):
            result = Runner.run_streamed(
                agent,
                input=inputs,
            )
            async for event in result.stream_events():
                if not isinstance(event, RawResponsesStreamEvent):
                    continue
                data = event.data
                if isinstance(data, ResponseTextDeltaEvent):
                    print(data.delta, end="", flush=True)
                elif isinstance(data, ResponseContentPartDoneEvent):
                    print("\n")

        inputs = result.to_input_list()
        print("\n")

        user_msg = input("Enter a message: ")
        inputs.append({"content": user_msg, "role": "user"})
        agent = result.current_agent


if __name__ == "__main__":
    asyncio.run(main())