# 作业1：语种判断 + 条件翻译
# 需求：
#   用户输入一段文本 -> 判断语种
#   - 非中文 -> 翻译成中文
#   - 中文   -> 翻译成英文
#   最终输出 JSON：{"query_input": "", "language": "", "result": ""}
#
# 运行：python 作业1_语种判断与翻译.py
# 依赖：pip install -qU openai-agents

import os
import json
import asyncio

# ---------------------------------------------------------------------------
# 1. 模型配置（DeepSeek 官方，OpenAI 兼容协议）
#    也可以改用课程里的 dashscope / qwen-max：把 BASE_URL 与 MODEL 换掉即可
# ---------------------------------------------------------------------------
os.environ.setdefault("OPENAI_API_KEY", "sk-9a390d6f613topabu2976c541ed77898bf")
os.environ.setdefault("OPENAI_BASE_URL", "https://api.deepseek.com")

from agents import Agent, Runner
from agents import set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions")  # 用 Chat Completions 协议，而非默认 Responses
set_tracing_disabled(True)                  # 关闭 SDK 链路追踪

MODEL = "deepseek-chat"

CHINESE_ALIASES = {"chinese", "mandarin", "中文", "汉语", "简体中文", "繁体中文", "simplified chinese", "traditional chinese"}


# ---------------------------------------------------------------------------
# 2. Agent 定义
# ---------------------------------------------------------------------------
# 2.1 语种识别 Agent
#     国内模型对 output_type（json_schema 结构化输出）支持不好，
#     所以这里让模型直接输出 JSON 文本，再用 json.loads 解析成数据。
language_agent = Agent(
    name="Language Detector",
    model=MODEL,
    instructions=(
        "You are a language identification expert.\n"
        "Detect the language of the user's text.\n"
        'Output ONLY a JSON object, no markdown, no extra words, format: {"language": "<language name>"}\n'
        "Use the English name of the language, e.g. Chinese, English, Japanese, French, Spanish, Russian, Korean, German, Arabic."
    ),
)

# 2.2 翻译 Agent
translator_agent = Agent(
    name="Translator",
    model=MODEL,
    instructions=(
        "You are a professional translator.\n"
        "Translate the user's text into the target language specified in the instruction.\n"
        "Output ONLY the translation, with no explanation, no quotes, no extra words."
    ),
)


# ---------------------------------------------------------------------------
# 3. 工具函数
# ---------------------------------------------------------------------------
def parse_json(raw: str) -> dict:
    """从模型输出里稳健地提取 JSON 对象（容忍 ```json 代码块等包裹）。"""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()
    left, right = s.find("{"), s.rfind("}")
    if left != -1 and right != -1:
        s = s[left:right + 1]
    return json.loads(s)


def is_chinese(language: str) -> bool:
    """判断识别出的语种是否为中文。"""
    return language.strip().lower() in CHINESE_ALIASES


# ---------------------------------------------------------------------------
# 4. 主流程：识别语种 -> 按规则确定目标语种 -> 翻译 -> 组装 JSON
# ---------------------------------------------------------------------------
async def run_translation(query_input: str) -> dict:
    # step 1: 识别语种
    det_result = await Runner.run(language_agent, query_input)
    language = parse_json(det_result.final_output).get("language", "").strip()

    # step 2: 规则路由——中文 -> 英文；非中文 -> 中文
    target = "English" if is_chinese(language) else "Chinese"

    # step 3: 翻译
    prompt = f"将下面的文本翻译成{target}，只输出译文：\n\n{query_input}"
    tr_result = await Runner.run(translator_agent, prompt)

    # step 4: 组装最终 JSON
    return {
        "query_input": query_input,
        "language": language,
        "result": tr_result.final_output.strip(),
    }


async def main():
    samples = [
        "今天天气真好，我们一起去公园散步吧。",                       # 中文 -> 英文
        "The quick brown fox jumps over the lazy dog.",             # 英文 -> 中文
        "こんにちは、お元気ですか？",                                  # 日文 -> 中文
        "Bonjour, comment allez-vous aujourd'hui ?",                # 法文 -> 中文
    ]

    for text in samples:
        output = await run_translation(text)
        print(json.dumps(output, ensure_ascii=False, indent=4))
        print("-" * 60)


if __name__ == "__main__":
    asyncio.run(main())
