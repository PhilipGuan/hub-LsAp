"""第 1 步 · 只做「语种识别」，把最难的一块单独跑通。

这一步要理解两件事：
    1. Agent 是"声明式"的 —— 你只描述它是什么、要输出什么形状，不写调用逻辑
    2. output_type 让模型直接吐出 Pydantic 对象，省掉自己解析 JSON

运行：python 01_语种识别_基础版.py
"""

import asyncio

from pydantic import BaseModel, Field

import config
from agents import Agent, Runner

instructions_ = (
        "你是语种识别器。判断用户输入文本的语言。"
        "language 用该语言的英文名，is_chinese 表示它是否为中文。"
        "以 JSON 格式返回结果。JSON格式如下："
    )

class LangDetect(BaseModel):
    """结构化输出契约：模型必须按这个形状回答。"""

    language: str = Field(description="语言的英文名，包括但不限于 Chinese / English / Japanese / French")
    is_chinese: bool = Field(description="输入文本是否为中文")


detector = Agent(  # 声明式：这里只"描述"这个 Agent 是什么，不会发起任何网络请求
    name="LangDetector",  # 身份标识：只影响日志 / trace / 后续 handoff 引用，不改变模型行为
    model=config.MODEL,  # str 类型，即请求体里的 "model" 字段；值来自 .env 的 DEEPSEEK_MODEL
    instructions=instructions_,  # 系统提示词，等价于 messages 里的 system 角色，定义它的角色与任务
    output_type=LangDetect,  # 输出契约：告诉模型要什么形状，并把返回的 JSON 反序列化成 LangDetect 对象
    model_settings=config.JSON_SETTINGS,  # DeepSeek 必需，见 config.py 注释
)


async def main() -> None:
    samples = [
        "今天天气真不错，适合出去走走。",
        "Hello, how are you doing today?",
        "Bonjour tout le monde",
        "こんにちは、元気ですか",
        "C'est une bonne journée !",
        "أهلاً وسهلاً"
    ]

    for text in samples:  
        result = await Runner.run(detector, text)  # 真正发 HTTP 请求的唯一一行；await 在此让出控制权等待返回
        payload: LangDetect = result.final_output  # 到这里已经是 LangDetect 对象，不是 str，也不是 JSON 字符串
        print(f"{text!r:<40} -> language={payload.language!r}, is_chinese={payload.is_chinese}")
        # !r = 用 repr() 显示（给字符串加上引号，方便看出边界）；:<40 = 左对齐、不足 40 字符用空格补齐，让箭头对齐


if __name__ == "__main__":
    asyncio.run(main())
