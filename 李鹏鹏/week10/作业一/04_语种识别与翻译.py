import os

# https://bailian.console.aliyun.com/?tab=model#/api-key
os.environ["OPENAI_API_KEY"] = "sk-bf4240833ba44d6ea4e6b33dacd6be11"
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

import asyncio
import json
from agents import Agent, Runner
from agents import set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions") # 强制 SDK 使用 Chat Completions API，而非默认的 Responses API。
set_tracing_disabled(True) # 彻底关闭 SDK 的链路追踪（Tracing） 功能


# 语种识别代理
# 注意：DeepSeek 不支持 response_format 的 json_schema 类型，因此不使用 output_type，
# 而是通过提示词约束输出格式，再手动解析 JSON
language_detect_agent = Agent(
    name="Language Detect Agent",
    model="deepseek-chat",
    instructions=(
        "识别用户输入文本的语种。"
        "只返回 JSON，格式为: {\"language\": \"语种名称（用中文表示，如：中文/英语/日语/法语等）\"}，不要输出其他内容"
    ),
)

# 中文翻译代理 - 把非中文文本翻译为中文
chinese_translator_agent = Agent(
    name="Chinese Translator",
    model="deepseek-chat",
    handoff_description="负责把非中文文本翻译为中文的专家代理。",
    instructions="您是专业的翻译专家。请将用户输入的文本准确地翻译为中文，只返回翻译结果，不要输出其他内容。",
)

# 英文翻译代理 - 把中文文本翻译为英文
english_translator_agent = Agent(
    name="English Translator",
    model="deepseek-chat",
    handoff_description="负责把中文文本翻译为英文的专家代理。",
    instructions="您是专业的翻译专家。请将用户输入的文本准确地翻译为英文，只返回翻译结果，不要输出其他内容。",
)


def parse_json(text: str) -> dict:
    """手动解析 JSON（容错：剥掉可能的 ```json 代码块包裹）"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json", "", 1).strip()
    return json.loads(text)


async def translate(query: str) -> dict:
    """
    流程：语种识别 agent -》 根据语种选择翻译 agent -》 组装最终 JSON
    这里语种路由在代码里完成（语种识别结果需要参与最终输出，用 handoffs 不好取回），
    效果上等同于 03_基础使用.py 中 triage_agent 的 handoffs 分派。
    """
    # 第一步：语种识别
    print("[Step 1] 正在识别语种...")
    result = await Runner.run(language_detect_agent, query)
    language = parse_json(result.final_output)["language"]
    print(f"[Step 1] 识别结果: {language}")

    # 第二步：根据语种选择翻译代理（非中文 -> 翻译为中文；中文 -> 翻译为英文）
    if language == "中文":
        print("[Step 2] 检测到中文，分派给英文翻译代理...")
        target_agent = english_translator_agent
    else:
        print(f"[Step 2] 检测到非中文（{language}），分派给中文翻译代理...")
        target_agent = chinese_translator_agent

    result = await Runner.run(target_agent, query)
    translation = result.final_output.strip()

    # 第三步：组装最终输出
    return {
        "query_input": query,
        "language": language,
        "result": translation,
    }


async def main():
    print("--- 启动语种识别与翻译代理系统 ---")

    # 先用几个示例跑一遍完整流程
    demo_queries = [
        "Hello world, this is a beautiful day!",
        "人工智能正在深刻地改变我们的生活方式。",
        "Bonjour, comment allez-vous?",
    ]
    for query in demo_queries:
        print("\n" + "="*50)
        print(f"**用户输入:** {query}")
        final_output = await translate(query)
        print("\n**✅ 最终输出:**")
        print(json.dumps(final_output, ensure_ascii=False, indent=4))

    # 交互模式：用户输入文本，回车执行；直接回车退出
    print("\n" + "="*50)
    print("进入交互模式，请输入文本（直接回车退出）：")
    while True:
        query = await asyncio.to_thread(input, "\n> ")
        query = query.strip()
        if not query:
            print("已退出交互模式。")
            break
        final_output = await translate(query)
        print("\n**✅ 最终输出:**")
        print(json.dumps(final_output, ensure_ascii=False, indent=4))


if __name__ == "__main__":
    asyncio.run(main())
