from openai import OpenAI


client = OpenAI(
    api_key="sk-723ccfbb9e7fxxxxxxx0a61f2",
    base_url="https://api.deepseek.com")

system_prompt = """
你是情感分析助手，从用户的描述中提取人物关系，以 JSON 数组格式输出。
每个关系包含: source(关系源)、relation(关系, 可以是爱慕或恨)、target(关系目标)。

JSON 输出示例：
[
    {"source": "小王", "relation": "恨", "target": "小华"}
]
"""

user_prompt = """
小明喜欢小姚，但是小姚喜欢小王。
"""

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    response_format={"type": "json_object"},
    max_tokens=500,
    temperature=0.0,
)

content = response.choices[0].message.content
print(content)
