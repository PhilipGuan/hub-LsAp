"""
情感分析智能体（简化版）— 用 LLM Tool Call 从文本中提取人物关系

输入：小明喜欢小姚，但是小姚喜欢小王。
输出：人物关系图谱 JSON
"""

import json
from openai import OpenAI

client = OpenAI(
    api_key="sk-86fec757dxxxabub0bf6a8a514d55a9c7",
    base_url="https://api.deepseek.com",
)

# ─────────────────────────────────────────────────────────────
# 第一步：定义工具函数（本地执行逻辑）
# ─────────────────────────────────────────────────────────────

def build_relationship_graph(relations: list[dict]) -> str:
    """接收人物关系列表，打印并返回确认信息。"""
    for rel in relations:
        print(f"  📊 {rel['source']} --[{rel['relation']}]--> {rel['target']}")
    return json.dumps({"status": "success", "count": len(relations)}, ensure_ascii=False)

# ─────────────────────────────────────────────────────────────
# 第二步：定义工具 schema（告诉模型有哪些工具可用）
# ─────────────────────────────────────────────────────────────

tools = [
    {
        "type": "function",
        "function": {
            "name": "build_relationship_graph",
            "description": "构建人物关系图谱",
            "parameters": {
                "type": "object",
                "properties": {
                    "relations": {
                        "type": "array",
                        "description": "人物关系列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source":   {"type": "string", "description": "关系主体（人名）"},
                                "relation": {"type": "string", "description": "关系类型，如爱慕、厌恶、朋友"},
                                "target":   {"type": "string", "description": "关系客体（人名）"},
                            },
                            "required": ["source", "relation", "target"],
                        },
                    },
                },
                "required": ["relations"],
            },
        },
    },
]

# ─────────────────────────────────────────────────────────────
# 第三步：调用 LLM，让它分析文本并调用工具
# ─────────────────────────────────────────────────────────────

user_input = "小明喜欢小姚，但是小姚喜欢小王。"
print(f"输入: {user_input}\n")

# 第一轮：LLM 分析文本，决定调用哪个工具、传什么参数
messages = [
    {"role": "system", "content": "你是情感关系分析助手。从文本中提取人物关系，调用 build_relationship_graph 工具。"},
    {"role": "user", "content": user_input},
]

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    tools=tools,
    temperature=0.0,
    extra_body={"thinking": {"type": "disabled"}},
)

msg = response.choices[0].message

# 如果模型返回了工具调用，就执行它
if msg.tool_calls:
    tc = msg.tool_calls[0]
    args = json.loads(tc.function.arguments)
    print(f"模型调用工具: {tc.function.name}")
    result = build_relationship_graph(args["relations"])

    # 把工具执行结果告诉模型，让它生成最终回复
    messages.append(msg.model_dump())
    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    final = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=messages,
        tools=tools,
        temperature=0.0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    print(f"\n模型总结: {final.choices[0].message.content}")
