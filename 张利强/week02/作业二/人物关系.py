"""
作业二：借助于llm tool call 或 json mode 能力，构建一个简单的情况情感分析智能体。提交实现代码。

输入：小明喜欢小姚，但是小姚喜欢小王。
输出：人物关系图谱

[
    {
        "source": "小明",
        "relation": "爱慕",
        "target": "小姚"
    }
]
"""
from openai import OpenAI
import json

client = OpenAI(
    api_key="sk-",
    base_url="https://api.deepseek.com"
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
你是一个专业的情感分析助手。
用户会提供一些文本。请从中提取出任务关系，并严格按照json格式返回结果。

输入示例：
小明喜欢小姚。

JSON 输出示例：
[
    {
        "source": "小明",
        "relation": "爱慕",
        "target": "小姚"
    }
]
"""

user_prompt = """
小明喜欢小姚，但是小姚喜欢小王。
"""

# user_prompt = """
# 小明喜欢小姚，但是小姚喜欢小王，小王不喜欢小姚
# """

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    stream=False,
    response_format={"type": "json_object"},
    # reasoning_effort="high",
    # extra_body={"thinking": {"type": "enabled"}}
)

content = response.choices[0].message.content
print(content)
result = safe_json_parse(content)
