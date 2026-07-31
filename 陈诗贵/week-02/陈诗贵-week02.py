import re
import json
from openai import OpenAI

def extract_relations(text: str) -> list[dict]:
    """基于规则的提取函数（保持不变）"""
    relation_map = {
        '喜欢': '喜欢',
        '讨厌': '讨厌',
        '爱慕': '喜欢',
        '憎恨': '讨厌',
        '暗恋': '喜欢',
    }
    relation_words = '|'.join(relation_map.keys())
    pattern = rf'([\u4e00-\u9fa5]{{2}})({relation_words})([\u4e00-\u9fa5]{{2,3}})'
    matches = re.findall(pattern, text)
    result = []
    for source, rel, target in matches:
        relation = relation_map.get(rel, rel)
        result.append({"source": source, "relation": relation, "target": target})
    return result

client = OpenAI(
    api_key="sk-114a0acxxxabuecc6296799d21a",
    base_url="https://api.deepseek.com",
)

TOOLS = [{
    "type": "function",
    "function": {
        "name": "extract_relations",
        "description": "从一段中文文本中提取人物之间的情感关系（如喜欢、讨厌等），返回一个关系列表。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "需要分析的中文原始文本"
                }
            },
            "required": ["text"]
        }
    }
}]

FUNCTION_MAP = {
    "extract_relations": extract_relations,
}

def run_tool_call(tc) -> str:
    """执行工具调用，返回字符串结果"""
    name = tc.function.name
    args = json.loads(tc.function.arguments)
    print(f"    → 调用工具: {name}({json.dumps(args, ensure_ascii=False)})")
    result = FUNCTION_MAP[name](**args)
    # 将结果转为 JSON 字符串，以便作为 tool 消息内容
    if isinstance(result, (list, dict)):
        result = json.dumps(result, ensure_ascii=False)
    print(f"    ← 结果: {result}")
    return result

print("=" * 65)
print("1️⃣  单工具调用 — 人物关系提取")
print("=" * 65)

messages = [
    {"role": "system", "content": "你是智能关系判断助手，可根据需要使用工具回答用户问题。"},
    {"role": "user", "content": "小明喜欢小姚，但是小姚喜欢小王"},
]

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    tools=TOOLS,
    temperature=0.0,
)

choice = response.choices[0]
msg = choice.message

if msg.tool_calls:
    for tc in msg.tool_calls:
        result = run_tool_call(tc)
        # 关键修复：将 msg 转为字典，而不是直接追加对象
        messages.append(msg.model_dump())   # 或 msg.dict()
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result,
        })

    # 再次调用模型生成最终回复
    final = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=messages,
        tools=TOOLS,
        temperature=0.0,
    )
    print(f"\n最终回复: {final.choices[0].message.content}")
else:
    print(f"直接回复: {msg.content}")

print()
