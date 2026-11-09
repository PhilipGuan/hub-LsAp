"""
作业1：语种识别 + 定向翻译（OpenAI Agents SDK）

需求：
    用户输入一段文本
      -> 判断这段文本是什么语言
      -> 如果不是中文，翻译成中文；如果是中文，翻译成英文
      -> 最终输出固定格式的 JSON：
         {
             "query_input": "",
             "language": "",
             "result": ""
         }

设计思路（“两阶段 + 代码分流”）：
    ① language_detector 用 output_type 出结构化结果（只判断语种，不翻译）
    ② 用 Python 的 if/else 决定走哪个翻译 Agent（分支由代码控制，不由模型决定）
    ③ 最终 JSON 由 Python 侧组装，保证键名、格式、中文编码 100% 可控

参考的课堂示例：
    Week10\\3-agent复杂案例\\30_串行编排.py      —— 确定性串行编排 + 代码分支门禁 + assert 校验
    Week10\\1-openai-agent使用\\03_基础使用.py   —— output_type 结构化输出（HomeworkOutput）
    Week10\\3-agent复杂案例\\33_并行调用.py      —— 多个专职 Agent 各干一件事
    Week10\\1-openai-agent使用\\05_流式输出.py   —— 反面参考：本作业要完整 JSON，所以不用流式

运行：
    python work1.py           # 交互式输入一段文本
    python work1.py --demo    # 跑内置的 5 组测试用例
"""

import os
import re
import sys
import asyncio
import traceback

# https://bailian.console.aliyun.com/?tab=model#/api-key
# 优先用系统环境变量里的 key，没有才用下面这个兜底（交作业/发上网前记得删掉明文 key）
os.environ.setdefault("OPENAI_API_KEY", "sk-aa76fcf6520f48d38b356ae436c16af0")
os.environ.setdefault("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

from pydantic import BaseModel, ValidationError
from agents import Agent, Runner, trace
from agents import set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions")  # qwen / deepseek 只支持 Chat Completions 协议
set_tracing_disabled(True)                  # 非官方端点必须关掉 tracing 上报

MODEL_NAME = "qwen-max"


# ==================== ① 结构化输出模型 ====================
class LanguageInfo(BaseModel):
    """语种识别的结构化输出（写法参考 03_基础使用.py 的 HomeworkOutput）"""
    language: str       # 语种名称：中文 / English / 日本語 ...
    is_chinese: bool    # True = 中文（简体或繁体）；False = 其他语种


class FinalOutput(BaseModel):
    """题目要求的最终输出结构，键名必须与要求完全一致"""
    query_input: str
    language: str
    result: str


# ==================== ② 三个专职 Agent ====================
# 只负责“判断”，不负责“翻译”
language_detector = Agent(
    name="language_detector",
    model=MODEL_NAME,
    instructions=(
        "你是语种识别专家，只负责判断用户输入文本主要使用的是哪种语言，不要做翻译。"
        "规则：简体中文、繁体中文都算中文，is_chinese 为 true；"
        "英文、日文、韩文、法文等其他语种都算非中文，is_chinese 为 false；"
        "中英混排时，以整句的主要表达语言为准。"
        "language 字段填写该语种自己的名字（中文填“中文”，英语填“English”，日语填“日本語”）。"
        "只输出 json，不要输出任何解释。"
    ),
    output_type=LanguageInfo,   # 国内模型对结构化输出支持一般，代码里另做了兜底解析
)

# 非中文 -> 中文
to_chinese_agent = Agent(
    name="to_chinese_agent",
    model=MODEL_NAME,
    instructions=(
        "你是翻译专家，把用户输入的文本翻译成简体中文。"
        "只输出译文本身，不要解释、不要加引号、不要 markdown 代码块。"
    ),
)

# 中文 -> 英文
to_english_agent = Agent(
    name="to_english_agent",
    model=MODEL_NAME,
    instructions=(
        "You are a translation expert. Translate the user's text into English. "
        "Output the translation only - no explanation, no quotes, no markdown."
    ),
)


# ==================== ③ 兜底解析 ====================
def parse_language_info(raw_output) -> LanguageInfo:
    """
    正常情况 final_output 已经是 LanguageInfo 对象；
    但国内模型有时不遵守 output_type，会返回一段带 json 的文本，这里做一层兜底。
    """
    if isinstance(raw_output, LanguageInfo):
        return raw_output

    text = str(raw_output)

    # 1) 从文本里抠出第一个 {...} 再尝试解析
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return LanguageInfo.model_validate_json(match.group())
        except ValidationError:
            pass

    # 2) 完全解析不出来时，退化成关键词判断，保证程序不崩
    lowered = text.lower()
    is_chinese = any(k in text for k in ("中文", "汉语", "简体", "繁体")) or "chinese" in lowered
    return LanguageInfo(language="中文" if is_chinese else "非中文（未识别出具体语种）",
                        is_chinese=is_chinese)


# ==================== ④ 主流程：串行编排 ====================
async def handle(user_input: str) -> FinalOutput:
    """一次完整的处理：识别语种 -> 代码分流 -> 翻译 -> 组装 JSON"""
    with trace("Homework1 语种识别与定向翻译"):
        # 1) 语种识别
        detect_result = await Runner.run(language_detector, user_input)
        lang = parse_language_info(detect_result.final_output)
        assert isinstance(lang, LanguageInfo), "语种识别结果格式异常"

        # 2) 代码分流：中文 -> 英文；非中文 -> 中文（这一步不交给模型决定）
        target_agent = to_english_agent if lang.is_chinese else to_chinese_agent

        # 3) 翻译
        translate_result = await Runner.run(target_agent, user_input)
        translated = str(translate_result.final_output).strip()

        # 4) Python 侧组装最终结果（键名、编码都可控）
        return FinalOutput(
            query_input=user_input,
            language=lang.language,
            result=translated,
        )


async def run_one(user_input: str) -> None:
    """处理并打印一条输入"""
    print("-" * 60)
    print(f"输入：{user_input}")
    try:
        final = await handle(user_input)
        # ensure_ascii=False 才不会把中文转义成 \u4e2d\u6587
        print(final.model_dump_json(indent=2, ensure_ascii=False))
    except Exception as e:
        traceback.print_exc()
        print(f"处理失败：{e}")


async def main() -> None:
    user_input = input("请输入一段文本（中文会翻成英文，其他语言会翻成中文）：\n").strip()

    if not user_input:
        print("输入为空，程序结束。")
        return

    await run_one(user_input)


async def demo() -> None:
    """内置测试用例，一条命令跑完全部分支"""
    test_inputs = [
        "人工智能正在改变世界。",                        # 中文 -> 英文
        "The quick brown fox jumps over the lazy dog.",  # 英文 -> 中文
        "こんにちは、お元気ですか。",                     # 日文 -> 中文
        "帮我翻译一下 hello world。",                     # 中英混排 -> 英文
        "12345 !!!",                                     # 界外输入：不崩就行
    ]
    for text in test_inputs:
        await run_one(text)


if __name__ == "__main__":
    if "--demo" in sys.argv[1:]:
        asyncio.run(demo())
    else:
        asyncio.run(main())