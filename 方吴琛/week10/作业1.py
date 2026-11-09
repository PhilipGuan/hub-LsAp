import asyncio
import json
from pydantic import BaseModel
from agents import Agent, Runner
from agents import set_default_openai_api, set_tracing_disabled

# 大模型调用协议：openai 官方默认走 Responses API，阿里云 dashscope 兼容的是 Chat Completions API
set_default_openai_api("chat_completions")  # 强制 SDK 使用 Chat Completions API
set_tracing_disabled(True)  # 关闭 SDK 的链路追踪


class LanguageDetectionOutput(BaseModel):
    """用于结构化输出：检测到的文本语种"""
    language: str  # 例如 "中文"、"英文"、"日文"、"法文" 等


# 语种检测代理，用 output_type 做结构化输出
detect_agent = Agent(
    name="Language Detect Agent",
    model="qwen-max",
    instructions=(
        "你的任务是判断用户输入文本的语种（是什么语言）。"
        "请用中文返回语种名称，例如：中文、英文、日文、法文、韩文、德文、西班牙文等。"
        "只判断主要语种，json 返回。"
    ),
    output_type=LanguageDetectionOutput,  # 国内模型对结构化输出支持一般，这里字段少、可接受
)

# 中文 -> 英文 翻译专家代理
to_english_agent = Agent(
    name="Chinese to English Agent",
    model="qwen-max",
    handoff_description="负责将中文文本翻译为英文的专家代理。",
    instructions="你是专业翻译。请将用户输入的中文文本翻译成流畅、地道的英文，只输出译文，不要添加任何解释或多余文字。",
)

# 非中文 -> 中文 翻译专家代理
to_chinese_agent = Agent(
    name="Other to Chinese Agent",
    model="qwen-max",
    handoff_description="负责将非中文文本翻译为中文的专家代理。",
    instructions="你是专业翻译。请将用户输入的非中文文本翻译成流畅、地道的中文，只输出译文，不要添加任何解释或多余文字。",
)


async def translate(query: str) -> dict:
    """
    主流程：
    1. 先用 detect_agent 检测语种
    2. 根据语种路由到对应翻译专家代理
    3. 组装成要求的 JSON 输出
    """
    # 第一步：检测语种
    detect_result = await Runner.run(detect_agent, query)
    language = detect_result.final_output_as(LanguageDetectionOutput).language

    # 第二步：根据语种路由 —— 中文译英文，非中文译中文
    if "中" in language:  # 中文 / 简体中文 / 繁体中文
        trans_result = await Runner.run(to_english_agent, query)
    else:
        trans_result = await Runner.run(to_chinese_agent, query)

    # 第三步：组装 JSON
    return {
        "query_input": query,
        "language": language,
        "result": trans_result.final_output.strip(),
    }


async def main():
    print("--- 启动语种检测与翻译代理系统 ---")

    # 测试用例：中文、英文、日文各一条
    test_cases = [
        "今天天气真不错，适合出去散步。",                      # 中文 -> 英文
        "The quick brown fox jumps over the lazy dog.",     # 英文 -> 中文
        "こんにちは、世界。これから始まる冒険だ。",               # 日文 -> 中文
    ]

    for query in test_cases:
        print("\n" + "=" * 60)
        print(f"输入: {query}")
        output = await translate(query)
        print("输出 JSON:")
        print(json.dumps(output, ensure_ascii=False, indent=4))


if __name__ == "__main__":
    asyncio.run(main())

