# -*- coding: utf-8 -*-
"""作业1：基于 OpenAI Agents 框架的语种判断 + 翻译应用。

流程：用户输入 -> 语种检测 Agent -> 条件翻译 Agent -> 结构化 JSON 输出。
输出格式：{"query_input": "", "language": "", "result": ""}
"""
import os

# DeepSeek（OpenAI 兼容协议）
# 注意：DeepSeek 不支持 response_format=json_schema，因此不使用 output_type，
# 改为提示词约定 JSON 输出 + 程序解析兜底。
os.environ["OPENAI_API_KEY"] = ""
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

import asyncio
import json
import re
from pydantic import BaseModel
from agents import Agent, Runner, set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions")  # 强制使用 Chat Completions API
set_tracing_disabled(True)  # 关闭链路追踪

MODEL = "deepseek-chat"


class TranslationResult(BaseModel):
    """作业要求的最终输出结构"""
    query_input: str
    language: str
    result: str


# 语种检测 Agent：只负责判断语种，回复纯文本
detect_agent = Agent(
    name="Language Detector",
    model=MODEL,
    instructions="判断用户输入文本的语种，只回复语种的中文名"
                 "（如：中文、英语、日语、韩语、法语），不要输出任何其他内容。"
                 "中英混合文本按占比更多的语种判断；实在无法判断时回复“未知”。",
)

# 翻译 Agent：按规则翻译，输出 JSON 文本
translate_agent = Agent(
    name="Translator",
    model=MODEL,
    instructions="你是专业翻译。翻译规则：如果原文是中文，翻译成英文；否则翻译成中文。"
                 "只输出如下 JSON，不要输出任何其他内容："
                 '{"query_input": "原文", "language": "检测语种", "result": "译文"}',
)


def _parse_json(text: str) -> dict:
    """从模型输出中提取 JSON（兼容 ```json 代码块包裹的情况）。"""
    match = re.search(r"\{.*\}", text, re.S)
    return json.loads(match.group(0))


async def translate_text(text: str) -> TranslationResult:
    """检测语种并按规则翻译，返回作业要求的 JSON 结构。"""
    # 边界用例：空输入兜底
    if not text.strip():
        return TranslationResult(query_input=text, language="未知", result="输入为空，无法翻译")

    # step1：检测语种
    detect_result = await Runner.run(detect_agent, text)
    language = detect_result.final_output.strip()

    # step2：按规则翻译
    prompt = f"原文：{text}\n检测语种：{language}\n请按规则翻译并输出JSON。"
    translate_result = await Runner.run(translate_agent, prompt)
    output = TranslationResult(**_parse_json(translate_result.final_output))

    # 由程序保证这两个字段与事实一致，不依赖模型自觉
    output.query_input = text
    output.language = language
    return output


async def main():
    test_cases = [
        "今天天气不错，适合出去散步。",        # 一般用例：中文 -> 英文
        "Hello, how are you today?",           # 一般用例：英文 -> 中文
        "こんにちは、元気ですか。",             # 一般用例：日语 -> 中文
        "我想买个iPhone，哪个型号好？",          # 边界用例：中英混合，主语种中文 -> 英文
        "好",                                 # 边界用例：极短文本
        "",                                   # 边界用例：空输入
    ]
    for text in test_cases:
        print("=" * 60)
        print(f"输入: {text!r}")
        output = await translate_text(text)
        print(output.model_dump_json(ensure_ascii=False, indent=4))


if __name__ == "__main__":
    asyncio.run(main())
