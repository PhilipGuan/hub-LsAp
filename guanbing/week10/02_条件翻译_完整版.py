"""第 2 步 · 完整作业：语种识别 + 条件翻译 + 固定 JSON 输出。

需求：
    用户输入一段文本
      -> 判断语种
      -> 非中文则翻译成中文；中文则翻译成英文
      -> 最终输出 {"query_input": ..., "language": ..., "result": ...}

设计要点（这是本练习最该带走的东西）：
    分支判断放在 **你的 Python 代码里**，不交给模型。
    模型只负责两件它擅长的事：识别语种、执行翻译。
    "谁来决定走哪条路" 由代码掌握 —— 这就是课程里讲的确定性编排，
    好处是可控、可测试、可断言，不会因为模型心情不好就乱走。

运行：python 02_条件翻译_完整版.py
"""

import asyncio
import json

from pydantic import BaseModel, Field

import config
from agents import Agent, Runner


# ---------- 数据契约 ----------
class LangDetect(BaseModel):
    language: str = Field(description="语言的英文名，例如 Chinese / English / Japanese / French")
    is_chinese: bool = Field(description="输入文本是否为中文")


class HomeworkOutput(BaseModel):
    """作业要求的最终输出格式。"""

    query_input: str
    language: str
    result: str


# ---------- Agent 声明 ----------
detector = Agent(
    name="LangDetector",
    model=config.MODEL,
    instructions=(
        "你是语种识别器。判断用户输入文本的语言。"
        "language 用该语言的英文名，is_chinese 表示它是否为中文。"
        "以 JSON 格式返回结果。"
    ),
    output_type=LangDetect,
    model_settings=config.JSON_SETTINGS,
)

translator = Agent(
    name="Translator",
    model=config.MODEL,
    # 这个 Agent 要的是纯文本译文，所以不带 JSON_SETTINGS
    instructions=(
        "你是专业翻译。严格按照用户指定的目标语言翻译给定内容。"
        "只输出译文本身，不要解释、不要引号、不要 markdown 代码块、不要重复原文。"
    ),
)


# ---------- 编排：代码控制流程 ----------
async def solve(text: str) -> HomeworkOutput:
    # 第 1 步：识别语种
    detect_result = await Runner.run(detector, text)
    lang: LangDetect = detect_result.final_output

    # 第 2 步：代码里的 if/else 决定目标语言
    target = "英文" if lang.is_chinese else "中文"

    # 第 3 步：让翻译 Agent 干活
    translate_result = await Runner.run(translator, f"目标语言：{target}\n待翻译内容：\n{text}")
    translated = str(translate_result.final_output).strip()

    # 第 4 步：组装成作业要求的形状
    return HomeworkOutput(
        query_input=text,
        language=lang.language,
        result=translated,
    )


async def main() -> None:
    samples = [
        "今天天气真不错，适合出去走走。",
        "Hello, how are you doing today?",
        "Bonjour tout le monde",
    ]

    print("=" * 70)
    print("批量样例")
    print("=" * 70)
    for text in samples:
        output = await solve(text)
        print(json.dumps(output.model_dump(), ensure_ascii=False, indent=2))
        print("-" * 70)

    # 手动输入模式：直接回车、Ctrl+D 或 Ctrl+C 退出
    print("\n输入任意文本试试（回车或 Ctrl+C 退出）：")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):  # EOFError = Ctrl+D
            print()
            break
        if not text:
            break
        output = await solve(text)
        print(json.dumps(output.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # 这里才是真正拦住 Ctrl+C 的地方，原因见下方说明。
        print("\n已中断，退出。")

# ── 为什么 Ctrl+C 必须在 asyncio.run() 外面兜底？──
# 实测结论：写在 main() 里面对 solve() 包 try/except KeyboardInterrupt 是无效的。
# 原因是 CPython 的 Task.__step() 对 KeyboardInterrupt / SystemExit 有特判：
#     except (KeyboardInterrupt, SystemExit):
#         super().set_exception(exc)
#         raise          # ← 直接向上抛，不交给协程自己的 except 处理
# 所以信号会在事件循环这一层就穿出去，协程内部根本收不到。
# 放在最外层包住 asyncio.run()，才是唯一可靠的位置。
