"""
关系情感分析 — 工具调用 (Tools / Function Calling)

把"非结构化文本"经由 LLM 的 tool call 收敛为"结构化人物关系图谱"。
文本（非结构化） -》 llm tool call -》 结构化函数参数

API 参考：https://platform.openai.com/docs/guides/function-calling
"""

import json
from openai import OpenAI

client = OpenAI(
    api_key="sk-86fec757d81a42b0bf6a8a514d55a9c7",  
    base_url="https://api.deepseek.com",
)


# ═════════════════════════════════════════════════════════════════════════════
# 0. 定义本地工具函数
# ═════════════════════════════════════════════════════════════════════════════

def extract_relationships(relationships: list) -> str:
    """接收模型抽出的关系列表，做校验/格式化（真正抽取由模型完成）。"""
    valid = []
    for r in relationships:
        if all(k in r for k in ("source", "relation", "target")):
            valid.append({
                "source": r["source"],
                "relation": r["relation"],
                "target": r["target"],
            })
    return json.dumps(valid, ensure_ascii=False)


# ═════════════════════════════════════════════════════════════════════════════
# 工具描述 schema（传给模型）
# ═════════════════════════════════════════════════════════════════════════════

# 大模型待选的工具列表，本质是一个 json
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "extract_relationships",
            "description": "从文本中抽取人物之间的情感/关系，输出人物关系图谱",
            "parameters": {
                "type": "object",
                "properties": {
                    "relationships": {
                        "type": "array",
                        "description": "人物关系边列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {
                                    "type": "string",
                                    "description": "关系主体（人物），如 小明",
                                },
                                "relation": {
                                    "type": "string",
                                    "description": "关系类型，如 爱慕/厌恶/朋友/亲人",
                                },
                                "target": {
                                    "type": "string",
                                    "description": "关系客体（人物），如 小姚",
                                },
                            },
                            "required": ["source", "relation", "target"],
                        },
                    }
                },
                "required": ["relationships"],
            },
        },
    },
]

# 工具名 → 本地函数映射
FUNCTION_MAP = {
    "extract_relationships": extract_relationships,
}


def run_tool_call(tc) -> str:
    """执行一次工具调用，返回结果字符串。"""
    name = tc.function.name
    args = json.loads(tc.function.arguments)
    print(f"    → 调用工具: {name}({json.dumps(args, ensure_ascii=False)})")
    result = FUNCTION_MAP[name](**args)
    print(f"    ← 结果: {result}")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 1. 强制工具调用 (tool_choice) — 文本 -> 人物关系图谱
# ═════════════════════════════════════════════════════════════════════════════

print("=" * 65)
print("1️⃣  关系情感分析 — 文本 -> 人物关系图谱")
print("=" * 65)

# 非结构化文本
text = "小明喜欢小姚，但是小姚喜欢小王。"

messages = [
    {"role": "system", "content": "你是关系抽取器。识别文本中的人物，并抽取他们之间的情感关系，"
                                   "方向为 source->target。'喜欢/爱/暗恋/中意' 归一化为 '爱慕'。"
                                   "只返回函数调用，不要解释。"},
    {"role": "user", "content": text},
]

# 强制让模型调用 extract_relationships，把自由文本收敛为结构化参数
# 注意：DeepSeek 的 thinking 模式不支持强制 tool_choice，需显式关闭思考
response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    tools=TOOLS,
    tool_choice={"type": "function", "function": {"name": "extract_relationships"}},
    temperature=0.0,
    extra_body={"thinking": {"type": "disabled"}},   # 关键：关闭思考模式
)

msg = response.choices[0].message

# 模型返回的是 tool_call（函数名 + 参数），本地执行即可得到图谱
if msg.tool_calls:
    for tc in msg.tool_calls:
        result = run_tool_call(tc)              # 本地执行，拿到关系图谱 JSON
        graph = json.loads(result)
        print("\n人物关系图谱:")
        print(json.dumps(graph, ensure_ascii=False, indent=2))
else:
    print(f"直接回复: {msg.content}")

print()


# ═════════════════════════════════════════════════════════════════════════════
# 2. 自动选择工具 (tool_choice="auto") — 模型自行决定是否抽取
# ═════════════════════════════════════════════════════════════════════════════

print("=" * 65)
print("2️⃣  自动工具调用 — 模型自行决定是否抽取")
print("=" * 65)

messages = [
    {"role": "system", "content": "你是关系抽取器。若文本中存在人物关系，请用 extract_relationships 输出图谱。"},
    {"role": "user", "content": "小明喜欢小姚，但是小姚喜欢小王。"},
]

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    tools=TOOLS,                  # tool_choice 默认 "auto"，模型自选
    temperature=0.0,
    extra_body={"thinking": {"type": "disabled"}},   # DeepSeek 下建议关闭思考以稳定使用工具
)

msg = response.choices[0].message
if msg.tool_calls:
    for tc in msg.tool_calls:
        result = run_tool_call(tc)
        print("\n人物关系图谱:")
        print(json.dumps(json.loads(result), ensure_ascii=False, indent=2))
else:
    print(f"直接回复: {msg.content}")
