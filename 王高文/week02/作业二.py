
import openai
import json

client = openai.OpenAI(
    api_key="sk-7bd42xxabua430e39",
    base_url="https://api.deepseek.com"
)

system_prompt ="""
你是一个情况情感分析大师，用户会提供人物关系请。从中解析出 "question" 和 "answer" 并以 JSON 格式输出。
输入示例：
小明喜欢小姚，但是小姚喜欢小王。

JSON输出示例：
[
     {
        "source": "小明",
        "relation": "爱慕",
        "target": "小姚"
    }
]
"""
user_prompt = "张三喜欢李四，李四喜欢小美"
response = client.chat.completions.create(
    model= "deepseek-v4-flash",
    messages=[
        {"role":"system","content":system_prompt},
        {"role":"user","content":user_prompt},
    ],
    response_format={"type": "json_object"},
    max_tokens=200,
    temperature=0.0
)

content = response.choices[0].message.content
results = json.loads(content)
print(results)
for result in results:
    print(result)

