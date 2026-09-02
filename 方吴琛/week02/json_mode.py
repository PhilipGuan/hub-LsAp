import json
from openai import OpenAI

client = OpenAI(
    api_key="sk-",
    base_url="https://api.deepseek.com",
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

system_prompt = """
用户会提供一些文本。请从中解析出 "source"、"relation" 和 "target" 并以 JSON 格式输出。用关键词爱慕代替喜欢。

输入示例：
小红喜欢小李

JSON 输出示例：
[
    {
        "source": "小红",
        "relation": "爱慕",
        "target": "小李"
    }
]
"""

user_prompt = "小明喜欢小姚，但是小姚喜欢小王。"

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    response_format={"type": "json_object"},
    max_tokens=200,
    temperature=0.0,
)

content = response.choices[0].message.content
result = safe_json_parse(content)

if result:
    print(f"\n人物关系图谱:")
    print(f"{json.dumps(result, ensure_ascii=False, indent=2)}")
else:
    print(f"  原始内容: {content}")

