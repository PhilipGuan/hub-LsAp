"""基于 OpenAI Agents 框架 + DeepSeek 的语种识别 + 翻译智能体。

功能：
    1. 识别用户输入文本的语种（detect_language 工具，由 langdetect 实现）。
    2. 若为中文，翻译为英文；若为非中文，翻译为中文。
    3. 以固定 JSON 结构输出结果。

运行前请设置环境变量 DEEPSEEK_API_KEY（或 OPENAI_API_KEY）。

注意（DeepSeek 兼容性）：
    - DeepSeek 不支持 OpenAI 的 json_schema 结构化输出，也不支持 Responses API，
      因此这里用 OpenAIChatCompletionsModel 强制走 Chat Completions，
      并通过 prompt 约束 + 手动解析 JSON 来得到结构化结果。
"""

import asyncio
import json
import os
import re
import sys

from langdetect import DetectorFactory, detect
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    Runner,
    function_tool,
    set_tracing_disabled,
)

# 加载 .env 文件中的环境变量（必须在使用 os.getenv 之前调用）
load_dotenv()

# DeepSeek 没有 OpenAI 的 trace 服务，关闭 tracing 避免无 key 时报错
set_tracing_disabled(disabled=True)

# 固定随机种子，保证 langdetect 检测结果稳定
DetectorFactory.seed = 0

# 语种代码 -> 可读名称 的映射
LANG_NAMES = {
    "zh-cn": "中文",
    "zh-tw": "中文",
    "zh": "中文",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "ru": "俄语",
    "it": "意大利语",
    "pt": "葡萄牙语",
    "ar": "阿拉伯语",
    "hi": "印地语",
    "th": "泰语",
    "vi": "越南语",
    "nl": "荷兰语",
}


@function_tool
def detect_language(text: str) -> str:
    """识别输入文本的语种，返回可读的语言名称（如 '中文'、'英语'、'日语'）。"""
    code = detect(text).lower()
    return LANG_NAMES.get(code, code)


class TranslationResult(BaseModel):
    """要求的输出结构。"""

    query_input: str  # 原始输入文本
    language: str     # 识别出的语种
    result: str       # 翻译结果


def _build_model() -> OpenAIChatCompletionsModel:
    """构造指向 DeepSeek 的模型对象，强制走 Chat Completions 接口。"""
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("请先设置环境变量 DEEPSEEK_API_KEY（或 OPENAI_API_KEY）")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
    )
    return OpenAIChatCompletionsModel(
        model="deepseek-chat",
        openai_client=client,
    )


# 翻译智能体：先调用语种识别工具，再根据结果决定翻译方向
agent = Agent(
    name="语种识别翻译智能体",
    instructions=(
        "你是一个翻译智能体，请严格按以下流程处理用户输入的文本：\n"
        "1. 调用 detect_language 工具识别文本语种。\n"
        "2. 若识别结果是「中文」，则将原文翻译为英文；"
        "若是其他语言（英语、日语、韩语、法语等），则翻译为中文。\n"
        "3. 最终只输出一个 JSON 对象，不要输出任何解释、Markdown 代码块或多余文字。\n"
        "JSON 对象包含三个字段：\n"
        "   - query_input: 用户原始输入文本（保持不变）\n"
        "   - language: 识别出的语种名称\n"
        "   - result: 翻译后的文本\n"
        "示例格式：{\"query_input\": \"你好\", \"language\": \"中文\", \"result\": \"Hello\"}"
    ),
    tools=[detect_language],
    model=_build_model(),
    model_settings=ModelSettings(temperature=0),
)


def _extract_json(text: str) -> dict:
    """从模型输出中稳健地提取 JSON（兼容可能出现的 Markdown 代码块）。"""
    text = text.strip()
    # 去掉 ```json ... ``` 包裹
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # 截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


async def run(text: str) -> TranslationResult:
    """运行智能体并返回结构化结果。"""
    result = await Runner.run(agent, text)
    data = _extract_json(result.final_output)
    return TranslationResult.model_validate(data)


def main() -> None:
    if len(sys.argv) > 1:
        text = sys.argv[1]
    else:
        text = input("请输入要处理的文本: ").strip()
        if not text:
            print("输入为空，退出。")
            return

    output = asyncio.run(run(text))
    print(json.dumps(output.model_dump(), ensure_ascii=False, indent=4))


if __name__ == "__main__":
    main()
