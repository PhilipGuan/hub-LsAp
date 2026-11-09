"""
作业1: 按照下openai agents框架，实现一个应用需求：用户输入文本，判断下文本的语种（是什么语言的）
然后如果是非中文的，则翻译为中文；如果是中文，则翻译为英文。最终输出格式需要为如下json：
{
    "query_input": "",
    "language": "",
    "result": ""
}
"""

import os

os.environ["OPENAI_API_KEY"] = "sk-2157e47c97topabu99d03aa43f4efe2f"
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

import asyncio
import json
import re

from pydantic import BaseModel
from agents import Agent, Runner
from agents import set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions")  # 强制使用 Chat Completions API
set_tracing_disabled(True)  # 关闭链路追踪

MODEL = "deepseek-flash"

class LanguageDetect(BaseModel):
    """语种识别结果"""
    language: str
    is_chinese: bool


class TranslateOutput(BaseModel):
    """最终要求的输出格式"""
    query_input: str
    language: str
    result: str


# Agent 定义
# 语种识别 Agent
language_detect_agent = Agent(
    name="language_detect_agent",
    model=MODEL,
    instructions=(
        "你是专业的语种识别专家。请判断用户输入文本使用的语种。"
        "严格按照如下 JSON 格式输出，不要输出任何多余内容\n"
        '{"language": "中文", "is_chinese": true}\n'
        "language 填写语种名称（如：中文、英语）；"
        "is_chinese 为布尔值，仅当语种为「中文」时为 true，其余情况为 false。"
    ),
)

# 翻译成中文 Agent
to_chinese_agent = Agent(
    name="to_chinese_agent",
    model=MODEL,
    instructions=(
        "你是专业的翻译官。请将用户输入的任意语种文本翻译为通顺、准确的简体中文。"
        "只输出译文本身，不要输出任何解释、引号或前后缀说明。"
    ),
)

# 翻译成英文 Agent
to_english_agent = Agent(
    name="to_english_agent",
    model=MODEL,
    instructions=(
        "你是专业的翻译官。请将用户输入的中文文本翻译为地道、准确的英文。"
        "只输出译文本身，不要输出任何解释、引号或前后缀说明。"
    ),
)


# 工具函数
def extract_json(text: str) -> dict:
    """从模型输出中解析出 JSON"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return {}


def to_bool(value) -> bool | None:
    """把模型可能输出的字符串布尔（"true"/"false"）安全地转为 bool，无法识别时返回 None"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "是"):
            return True
        if v in ("false", "0", "no", "否"):
            return False
    return None



def to_output(query: str, detect: LanguageDetect, translated: str) -> TranslateOutput:
    return TranslateOutput(
        query_input=query,
        language=detect.language,
        result=translated,
    )


# 串行编排
async def translate_pipeline(query: str) -> TranslateOutput:
    # 步骤一：识别语种
    detect_result = await Runner.run(language_detect_agent, query)
    info = extract_json(str(detect_result.final_output))

    language = info.get("language")
    is_chinese = to_bool(info.get("is_chinese"))
    detect = LanguageDetect(language=str(language).strip(), is_chinese=is_chinese)

    print(f"[语种识别] 输入语种：{detect.language}（is_chinese={detect.is_chinese}）")

    # 按语种路由到不同的翻译 Agent
    if detect.is_chinese:
        translate_agent = to_english_agent
        target = "英文"
    else:
        translate_agent = to_chinese_agent
        target = "中文"

    translate_result = await Runner.run(translate_agent, query)
    translated = str(translate_result.final_output).strip()
    print(f"[翻译执行] 已翻译为{target}：{translated}")

    # 步骤三：组装最终 JSON
    return to_output(query, detect, translated)


# 运行入口
async def main():
    print("--- 语种识别 + 翻译agent示例 ---")
    try:
        query = "今天天气真不错"
        print("\n" + "=" * 60)
        print(f"用户输入：{query}")
        output = await translate_pipeline(query)
        print("最终输出：")
        print(json.dumps(output.model_dump(), ensure_ascii=False, indent=4))
    except InputGuardrailTripwireTriggered as e:
        print("\n**❌ 守卫阻断触发:**", e)



if __name__ == "__main__":
    asyncio.run(main())
