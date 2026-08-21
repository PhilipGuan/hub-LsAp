"""第五周作业2：通过 OpenAI 兼容 SDK 调用本地 Ollama。"""
from pathlib import Path

from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="1111",  # Ollama 本地服务不校验真实 OpenAI API Key
)

response = client.chat.completions.create(
    model="qwen3:0.6b",
    messages=[
        {"role": "system", "content": "你是一个有帮助的助手。请使用简洁中文回答。"},
        {"role": "user", "content": "你好，请用一句话介绍你自己。"},
    ],
    temperature=0.7,
    max_tokens=512,
)

answer = response.choices[0].message.content
lines = [
    "模型: qwen3:0.6b",
    "接口: http://localhost:11434/v1/chat/completions",
    "用户: 你好，请用一句话介绍你自己。",
    f"助手: {answer}",
]
print("\n".join(lines))
Path(__file__).with_name("ollama_result.txt").write_text("\n".join(lines), encoding="utf-8")
