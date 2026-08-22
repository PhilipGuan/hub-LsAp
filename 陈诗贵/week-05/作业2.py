import sys

import httpx
from openai import OpenAI

# 让 print 使用 UTF-8 输出，避免模型回复里的 emoji 在 GBK 控制台报编码错误
sys.stdout.reconfigure(encoding="utf-8")

# 初始化客户端，指向 Ollama 的本地服务
client = OpenAI(
    base_url="http://localhost:11434/v1",  # Ollama API 地址
    api_key="1111",  # Ollama 默认无需真实 API Key，填任意值即可
    # 关键：trust_env=False 让 SDK 不读取系统代理，直连本地 Ollama，
    # 否则 httpx 会把 localhost 请求也发到系统代理(127.0.0.1:7890)导致 502
    http_client=httpx.Client(trust_env=False),
)

# 发送请求
response = client.chat.completions.create(
    model="qwen3:0.6b",  # 指定模型
    messages=[
        {"role": "system", "content": "你是一个有帮助的助手。"},
        {"role": "user", "content": "介绍一下你自己"}
    ],
    temperature=0.7,  # 控制生成多样性
    max_tokens=512    # 最大生成 token 数
)

# 打印结果
print(response.choices[0].message.content)