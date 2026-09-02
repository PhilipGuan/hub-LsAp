import json
from openai import OpenAI

client = OpenAI(
    api_key="sk-",
    base_url="https://api.deepseek.com",
)


# 1、 定义本地工具函数
# ---------- 构建人物关系图谱 ----------
def get_relation_graph(source, relation, target):
    """ 输出结构化的人物关系图谱 """

    res = [{
        "source": source,
        "relation": relation,
        "target": target
    }]
    return str(res)


# 2、 工具描述 schema（传给模型）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_relation_graph",
            "description": "获取两个人的关系图谱，以LIST[JSON]结构化输出",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "人物名称，例如小明、小王",
                    },
                    "relation": {
                        "type": "string",
                        "description": "人物之间的关系词，例如爱慕、朋友、父母",
                    },
                    "target": {
                        "type": "string",
                        "description": "人物名称，例如小张、小利",
                    },

                },
                "required": ["source", "relation", "target"]
            },
        }
    },
]

# 工具名 → 本地函数映射
FUNCTION_MAP = {
    "get_relation_graph": get_relation_graph,
}


messages = [
    {"role": "system","content": "你是智能文本识别，可根据需要文本找出对应人物关系, 只输出结构化结果，用关键词爱慕代替喜欢"},
    {"role": "user", "content": "小明喜欢小姚，但是小姚喜欢小王。"},
]

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    tools=TOOLS,
    temperature=0.0,
)

choice = response.choices[0]
msg = choice.message
messages.append(msg)  # 保留 assistant 的 tool_calls

if msg.tool_calls:
    for tc in msg.tool_calls:
        name = tc.function.name
        args = json.loads(tc.function.arguments)
        result = FUNCTION_MAP[name](**args)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result,
        })

    # 把工具结果发回模型，让其生成最终回复
    final = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=messages,
        tools=TOOLS,
        temperature=0.0,
    )
    print(f"\n 人物关系图谱 \n {final.choices[0].message.content}")
else:
    print(f"直接回复: {msg.content}")
