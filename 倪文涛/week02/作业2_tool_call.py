from openai import OpenAI
import json

# 创建 OpenAI 客户端
client = OpenAI(
    api_key="sk-xxxxabux",
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

def extract_relation(source: str, relation: str, target: str) -> str:
    """
    根据输入的来源人物、关系和目标人物，返回人物关系字典
    """
    return str({"source": source, "relation": relation, "target": target})

# 定义工具
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "extract_relation",
            "description": "根据输入的来源人物、关系和目标人物，返回人物关系字典",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "来源人物"},
                    "relation": {"type": "string", "description": "关系：爱慕/讨厌"},
                    "target": {"type": "string", "description": "目标人物"},
                },
                "required": ["source", "relation", "target"],
            },
        },
    }
]

# 定义工具函数映射
FUNCTION_MAP = {
    "extract_relation": extract_relation
}

def run_tool_call(tool_call) -> dict:
    """
    根据工具调用，返回工具调用结果
    """
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    return FUNCTION_MAP[name](**args)

print("=" * 50)
print("2. Tool Call 方式")
print("=" * 50)
# 定义系统提示词
system_prompt = """
你是一个人物关系分析专家，需要使用工具获取人物关系，并将最终结果以 JSON 数组格式输出不要包含其他内容。

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

# 创建对话消息列表
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_prompt},
]

# 调用大模型
response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    tools=TOOLS,
    temperature=0.0,
)

# 解析大模型输出
choice = response.choices[0]
msg = choice.message

# 将大模型输出添加到对话消息列表中
messages.append(msg)

if msg.tool_calls:
    # 遍历工具调用并执行
    for tool_call in msg.tool_calls:
        result = run_tool_call(tool_call)
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result,
        })

    # 把工具结果发回模型，让其生成最终回复
    final = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=messages,
        temperature=0.0,
    )
    # 解析大模型输出的 JSON
    results = json_parse(final.choices[0].message.content)
    print(f"用户输入：{user_prompt}")
    print(f"大模型输出结果：{results}")
else:
    print("没有调用工具")
