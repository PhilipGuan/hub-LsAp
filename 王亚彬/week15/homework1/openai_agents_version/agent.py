"""OpenAI Agents SDK 版：进程1 情感识别 Agent → 进程2 情绪回应 Agent"""
import os

from agents import Agent, Runner

MODEL = os.getenv("MODEL", "gpt-4o-mini")
LABELS = ("正向", "负面", "中性")

# 进程1：情感识别
sentiment_agent = Agent(
    name="情感识别",
    model=MODEL,
    instructions=(
        "你是情感分析器。判断用户输入的总体情感倾向。\n"
        "只输出一个词：正向 / 负面 / 中性，不要输出任何解释。"
    ),
)

# 进程2：正向则鼓励，负面则排解
reply_agent = Agent(
    name="情绪回应",
    model=MODEL,
    instructions=(
        "你是温暖的情绪陪伴者。输入中会给出【用户原话】和【情感识别结果】。\n"
        "若为正向：真诚鼓励、肯定，回应具体内容，避免空泛套话。\n"
        "若为负面：先共情安抚，再帮其拆解情绪，给出 1-2 条可操作建议。\n"
        "若为中性：友好自然地回应，顺着话题继续聊。\n"
        "回复控制在 150 字以内，语气亲切，不要提及'情感识别'这类内部术语。"
    ),
)


async def run_pipeline(text: str) -> dict:
    """后端编排：先跑进程1，再把它结果喂给进程2"""
    s = await Runner.run(sentiment_agent, text)
    label = next((k for k in LABELS if k in str(s.final_output)), "中性")

    r = await Runner.run(reply_agent, f"【用户原话】{text}\n【情感识别结果】{label}")
    return {"sentiment": label, "reply": str(r.final_output)}
