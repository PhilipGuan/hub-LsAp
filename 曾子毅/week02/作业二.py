from openai import OpenAI
import json

client = OpenAI(
    api_key="sk-a284xxxabudadcdc2035468d8",
    base_url="https://api.deepseek.com",
)

system_prompt = """你是一个情感分析助手，请从用户输入的文本中提取所有人际关系。
关系类型包括：爱慕、厌恶、憎恨、关心、思念、帮助。
- 喜欢、爱、爱慕、暗恋等 -> 爱慕
- 讨厌、厌恶、反感等 -> 厌恶
- 憎恨、恨等 -> 憎恨
- 关心、在乎等 -> 关心
- 想念、思念等 -> 思念
- 帮助、支持等 -> 帮助"""

tools = [
    {
        "type": "function",
        "function": {
            "name": "extract_relationships",
            "description": "从用户输入的情感文本中提取所有人际关系",
            "parameters": {
                "type": "object",
                "properties": {
                    "relationships": {
                        "type": "array",
                        "description": "提取到的人际关系列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {
                                    "type": "string",
                                    "description": "一段关系的主体人名，如'小明'",
                                },
                                "relation": {
                                    "type": "string",
                                    "description": "关系关键词",
                                    "enum": ["爱慕", "厌恶", "憎恨", "关心", "思念", "帮助"],
                                },
                                "target": {
                                    "type": "string",
                                    "description": "一段关系的客体人名，如'小姚'",
                                },
                            },
                            "required": ["source", "relation", "target"],
                        },
                    }
                },
                "required": ["relationships"],
            },
        },
    }
]


def safe_json_parse(text):
    if not text or not text.strip():
        print("    ⚠️  模型返回了空内容")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"    ⚠️  JSON 解析失败: {e}")
        cleaned = (
            text.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            print(f"    原始内容: {text[:200]}")
            return None


def analyze_emotion(prompt):
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=messages,
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "extract_relationships"}},
        max_tokens=1000,
        temperature=0.0,
        extra_body={"thinking": {"type": "disabled"}},
    )

    message = response.choices[0].message

    if not message.tool_calls:
        print("模型未调用工具，返回内容:", message.content)
        return []

    tool_call = message.tool_calls[0]
    print(f"模型调用工具: {tool_call.function.name}")

    result = safe_json_parse(tool_call.function.arguments)
    if result is None:
        return []

    return result.get("relationships", [])


def main():
    print("=" * 50)
    print("情感分析智能体")
    print("=" * 50)

    test_cases = [
        "小明喜欢小姚，但是小姚喜欢小王",
        "小明喜欢小姚，但是小姚喜欢小王，小姚不喜欢小明",
    ]

    for i, text in enumerate(test_cases, 1):
        print(f"\n--- 测试用例 {i} ---")
        print(f"输入：{text}")
        relations = analyze_emotion(text)
        print(f"输出：人物关系图谱")
        print("-" * 30)
        print(json.dumps(relations, ensure_ascii=False, indent=2))

    print("\n" + "=" * 50)
    while True:
        text = input("\n请输入情感文本（输入 '退出' 结束）：")
        if text.strip() == "退出":
            print("再见！")
            break

        relations = analyze_emotion(text)
        print("\n输出：人物关系图谱")
        print("-" * 30)
        print(json.dumps(relations, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
