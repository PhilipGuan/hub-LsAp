"""后端进程2：接收进程1的情感识别任务，使用 OpenAI Agents SDK 接入 DeepSeek 进行情感识别，并将结果发送给进程3。"""
import json
import os
import re
from pathlib import Path

import httpx
import uvicorn
from agents import (
    Agent,
    Runner,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from dotenv import load_dotenv
from fastapi import FastAPI
from openai import AsyncOpenAI
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

PROCESS3_URL = "http://127.0.0.1:8003/process"

if not os.environ.get("DEEPSEEK_API_KEY"):
    raise RuntimeError("未配置 DEEPSEEK_API_KEY，请在 src/backend/.env 中设置")

client = AsyncOpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)
set_default_openai_client(client, use_for_tracing=False)
set_default_openai_api("chat_completions")  # DeepSeek 只支持 chat completions，不支持 responses
set_tracing_disabled(True)

recognizer = Agent(
    name="emotion_recognizer",
    instructions=(
        "你是一个情感识别助手。分析用户输入的情感描述，"
        "只输出一个 JSON 对象，不要输出其他内容，格式为："
        '{"emotion": "情感类别，如 开心/难过/焦虑/愤怒/平静", '
        '"polarity": "positive 或 negative 或 neutral", '
        '"summary": "用一句话总结用户的情感状态"}'
    ),
    model="deepseek-chat",
)

app = FastAPI(title="process2_recognition")


class AnalyzeRequest(BaseModel):
    text: str


def parse_json(raw: str) -> dict:
    """从模型输出中提取 JSON 对象，兼容 markdown 代码块包裹的情况。"""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"模型输出不是 JSON：{raw}")
    return json.loads(cleaned[start : end + 1])


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    result = await Runner.run(recognizer, input=req.text)
    recognition = parse_json(result.final_output)
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(PROCESS3_URL, json=recognition)
        resp.raise_for_status()
    reply = resp.json()
    return {**recognition, **reply}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8002)
