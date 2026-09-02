from openai import OpenAI
import json

# ==================== 配置 ====================
client = OpenAI(
    api_key='sk-e11b9e73xxxabua8f6ab575c143c3c',
    base_url="https://api.deepseek.com"
)

# 本地工具函数
def get_love(relations: list) -> list:
    """
    本地工具函数：模型调用工具时，实际执行的代码逻辑
    :param relations: 模型提取并传入的人物关系列表
    :return: 处理后的关系数据
    """
    for idx, rel in enumerate(relations, 1):
        print(f"{idx}. {rel['source']} --[{rel['relation']}]--> {rel['target']}")
    return relations

# 工具定义（给模型读取的schema）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_love",
            "description": "从文本中提取所有人物之间的情感/社交关系，生成结构化的人物关系图谱",
            "parameters": {
                "type": "object",
                "properties": {
                    "relations": {
                        "type": "array",
                        "description": "人物关系列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string", "description": "关系发起方"},
                                "relation": {"type": "string", "description": "具体关系类型"},
                                "target": {"type": "string", "description": "关系接收方"}
                            },
                            "required": ["source", "relation", "target"]
                        }
                    }
                },
                "required": ["relations"]
            }
        }
    }
]

# 工具名 -> 本地函数映射（规范化管理）
FUNCTION_MAP = {
    "get_love": get_love,
}

def run_agent(text: str):
    messages = [
        {
            "role": "system",
            "content": "你是专业的人物关系分析专家，请精准识别文本中的人物和他们的情感关系。"  #提示词
        },
        {"role": "user", "content": text},
    ]

    try:
        response = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=messages,
            tools=TOOLS,
            stream=False,
        )
    # 处理异常
    except Exception as e:
        print(f"API请求异常: {e}")
        return []

    response_msg = response.choices[0].message
    tool_calls = response_msg.tool_calls

    if tool_calls:
        for call in tool_calls:
            func_name = call.function.name
            try:
                func_args = json.loads(call.function.arguments)
            except json.JSONDecodeError as e:
                print("工具参数JSON解析失败", e)
                continue

            # 使用映射表调用
            if func_name in FUNCTION_MAP:
                func = FUNCTION_MAP[func_name]
                result = func(func_args["relations"])
                return result
    else:
        print("模型未调用工具，直接返回文本：", response_msg.content)
        return [{"raw_response": response_msg.content}]


if __name__ == "__main__":
    user_input = input("请输入你的问题：")
    graph_result = run_agent(user_input)
    print("\n【最终关系图谱JSON】")
    print(json.dumps(graph_result, ensure_ascii=False, indent=2))
