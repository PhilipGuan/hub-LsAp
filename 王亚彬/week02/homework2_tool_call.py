import json

from src.client import LLMClient

def submit_relationship_graph(
    relationships: list[dict],
) -> list[dict]:
    """接收并返回人物关系图谱。"""
    return relationships

FUNCTION_MAP = {
    "submit_relationship_graph": submit_relationship_graph,
}

TOOLS = [
    {
        "name": "submit_relationship_graph",
        "description": (
            "提交从情境文本中提取出的完整人物关系图谱。"
            "必须提取文本中所有明确出现的人物关系，不能遗漏，不能推测。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relationships": {
                    "type": "array",
                    "description": "文本中明确出现的所有人物关系",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string",
                                "description": "关系的发起者",
                            },
                            "relation": {
                                "type": "string",
                                "description": (
                                    "标准化的关系名称，例如："
                                    "爱慕、朋友、亲属、同事、敌对"
                                ),
                            },
                            "target": {
                                "type": "string",
                                "description": "关系指向的人物",
                            },
                        },
                        "required": [
                            "source",
                            "relation",
                            "target",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["relationships"],
            "additionalProperties": False,
        },
    }
]

def extract_relationships(text: str) -> list[dict]:
    """调用 MiniMax M3 提取人物关系。"""

    llm = LLMClient()

    response = llm.client.messages.create(
        model=llm.model,
        max_tokens=1000,
        system=(
            "你是一个人物关系抽取智能体。"
            "请抽取文本中所有明确表达的人物关系。"
            "将“喜欢”“暗恋”等关系统一表示为“爱慕”。"
            "只抽取文本中明确出现的内容，不允许自行推测。"
            "完成分析后必须调用 submit_relationship_graph 工具。"
        ),
        messages=[
            {
                "role": "user",
                "content": text,
            }
        ],
        tools=TOOLS,
        tool_choice={
            "type": "tool",
            "name": "submit_relationship_graph",
        },
    )

    for block in response.content:
        if block.type != "tool_use":
            continue

        if block.name not in FUNCTION_MAP:
            raise RuntimeError(f"未知工具：{block.name}")

        tool_function = FUNCTION_MAP[block.name]
        result = tool_function(**block.input)
        return result

    raise RuntimeError("模型没有返回 Tool Call")


def main():
    text = input("请输入情境文本：").strip()
    relationships = extract_relationships(text)
    print("\n人物关系图谱：")
    print(
        json.dumps(
            relationships,
            ensure_ascii=False,
            indent=4,
        )
    )


if __name__ == "__main__":
    main()