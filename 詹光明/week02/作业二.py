from openai import OpenAI
import json


client = OpenAI(
    api_key="sk-2157e4xxxabuad99d03aa43f4efe2f",
    base_url="https://api.deepseek.com",
)

prompt = "小明喜欢小姚，但是小姚喜欢小王"
# prompt = "小明喜欢小姚，但是小姚喜欢小王，小姚不喜欢小明"

system_json = """
你是一个情感分析助手。请从用户输入的文本中提取所有人际关系，并以JSON数组格式输出，每个关系对象包含三个字段：
- source: 一段关系的主体人名，如“小明”
- relation: 关系关键词，如“爱慕”、“讨厌”
- target: 一段关系的客体人名，如“小姚”

输入示例：小明喜欢小姚

输出示例：
[
    {
        "source": "小明",
        "relation": "爱慕",
        "target": "小姚"
    }
]
"""

res_json = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[
        {"role": "system", "content": system_json},
        {"role": "user", "content": prompt},
    ],
    response_format={"type": "json_object"},
    max_tokens=1000,
    temperature=0.0,
)


def safe_json_parse(text: str) -> dict | list | None:
    """安全解析 JSON，处理可能的空 content 和格式异常。"""
    if not text or not text.strip():
        print("    ⚠️  模型返回了空 content（JSON 模式偶发问题）")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"    ⚠️  JSON 解析失败: {e}")
        # 尝试修复常见问题：删除 markdown 代码块标记
        cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            print(f"    原始内容: {text[:200]}")
            return None

content = res_json.choices[0].message.content
result = safe_json_parse(content)

print(f"解析结果:\n{content}")
