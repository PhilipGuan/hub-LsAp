from openai import OpenAI
import json

# 创建 OpenAI 客户端
client = OpenAI(
    api_key="sk-xxx",
    base_url="https://api.deepseek.com",
)

def json_parse(json_str: str) -> dict | list | None:
    """
    安全解析 JSON，处理可能的空 content 和格式异常。
    """
    try:
        if not json_str or not json_str.strip():
            print("大模型返回了空内容")
            return None
        data = json.loads(json_str)
        return json.dumps(data, indent=4, ensure_ascii=False)
    except json.JSONDecodeError:
        clean_json_str = json_str.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            data = json.loads(clean_json_str)
            return json.dumps(data, indent=4, ensure_ascii=False)
        except json.JSONDecodeError:
            print(f"大模型返回了无法解析的 JSON，原始内容：{json_str[:1000]}")
            return None

print("=" * 50)
print("1. Json Mode方式")
print("=" * 50)
# 定义系统提示词
system_prompt = """
你是一个人物关系分析专家，从用户给定的文字描述中提取人物关系，以 JSON 数组格式输出，包含以下字段：
- source（来源人物）
- relation（关系：爱慕/讨厌）
- target（目标人物）

JSON 输出示例：
[
    {
        "source": "张三",
        "relation": "爱慕",
        "target": "李四"
    }
]
"""

# 定义用户输入
user_prompt = "小明喜欢小姚，但是小姚喜欢小王。"

# 调用大模型（非流式）
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
# 解析大模型输出的 JSON
results = json_parse(response.choices[0].message.content)
print(f"用户输入：{user_prompt}")
print(f"大模型输出结果：{results}")
