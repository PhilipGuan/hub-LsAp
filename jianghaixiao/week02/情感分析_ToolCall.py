"""
工具调用 (Tool Call) 版 — 人物关系情感分析智能体

原理：
  定义 extract_relationship 工具，模型通过多次调用该工具，
  将非结构化文本中的人物关系逐一"注册"出来，实现结构化抽取。

输入示例：
  小明喜欢小姚，但是小姚喜欢小王。

输出示例：
  [
      {"source": "小明", "relation": "爱慕", "target": "小姚"},
      {"source": "小姚", "relation": "爱慕", "target": "小王"}
  ]
"""

import json
from openai import OpenAI

client = OpenAI(
    api_key="sk-e16dfcaa8c7e439xxxabu922f96c4a8f",
    base_url="https://api.deepseek.com",
)


# ════════════════════════════════════════════════════════════════════════
# 1. 定义工具：extract_relationship
# ════════════════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "extract_relationship",
            "description": "从文本中提取一段人物关系。每次调用记录一条有向关系。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "关系的主体（发出情感的一方），如 小明、张三",
                    },
                    "relation": {
                        "type": "string",
                        "description": "关系类型，如 爱慕、喜欢、讨厌、暗恋",
                    },
                    "target": {
                        "type": "string",
                        "description": "关系的客体（承受情感的一方），如 小姚、李四",
                    },
                },
                "required": ["source", "relation", "target"],
            },
        },
    },
]

# 情感词归一化映射
RELATION_NORMALIZE = {
    "喜欢": "爱慕",
    "爱慕": "爱慕",
    "暗恋": "暗恋",
    "讨厌": "讨厌",
    "厌恶": "讨厌",
    "不喜欢": "讨厌",
    "恨": "憎恨",
    "憎恨": "憎恨",
    "关心": "关心",
    "关怀": "关心",
    "照顾": "关心",
}


def normalize_relation(rel: str) -> str:
    """将模型输出的关系词归一化为标准情感标签。"""
    return RELATION_NORMALIZE.get(rel, rel)


# ════════════════════════════════════════════════════════════════════════
# 2. 核心：通过 Tool Call 提取关系
# ════════════════════════════════════════════════════════════════════════

def analyze_relationships(text: str) -> list[dict]:
    """主流程：将文本送入模型，通过多次 tool_call 提取所有人物关系。"""

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个人物关系分析专家。\n\n"
                "你的任务是从用户提供的文本中，逐条提取人物之间的情感关系。\n"
                "每发现一段关系，就调用一次 extract_relationship 工具来记录。\n\n"
                "规则：\n"
                "1. 关系通常表现为「A 喜欢/爱慕/讨厌 B」这样的句式。\n"
                "2. 如果有多条关系，请多次调用工具（可并行）。\n"
                "3. source 是情感发出方，target 是情感接收方。\n"
                "4. relation 使用最贴切的情感词：喜欢、爱慕、暗恋、讨厌、关心等。\n"
                "5. 不要添加额外文本解释，只通过工具调用输出结果。"
            ),
        },
        {"role": "user", "content": text},
    ]

    relationships = []
    max_turns = 10

    for turn in range(max_turns):
        response = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=messages,
            tools=TOOLS,
            temperature=0.1,
        )

        choice = response.choices[0]
        msg = choice.message

        if not msg.tool_calls:
            # 模型不再调用工具 → 提取结束
            if turn == 0:
                # 第一轮就没有工具调用，可能是模型直接回复了文本
                print(f"  （模型直接回复）{msg.content}")
            break

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            source = args.get("source", "").strip()
            relation = normalize_relation(args.get("relation", "").strip())
            target = args.get("target", "").strip()

            relationships.append({
                "source": source,
                "relation": relation,
                "target": target,
            })

            print(f"  ✓ 发现关系: 「{source}」——[{relation}]——→「{target}」")

            # 将工具调用结果注入对话，让模型可以继续抽取
            messages.append(msg)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps({"status": "recorded"}, ensure_ascii=False),
            })

    return relationships


# ════════════════════════════════════════════════════════════════════════
# 3. 主程序入口
# ════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 55)
    print("  人物关系情感分析智能体（Tool Call 版）")
    print("=" * 55)
    print()

    text = input("请输入一段描述人物关系的文本（直接回车使用示例）：\n> ").strip()
    if not text:
        text = "小明喜欢小姚，但是小姚喜欢小王。"
        print(f"  使用默认示例：{text}")
    print()

    print("🔍 模型正在分析人物关系……\n")
    relations = analyze_relationships(text)

    print()
    if relations:
        print(f"✅ 共发现 {len(relations)} 条关系，人物关系图谱如下：")
        print(json.dumps(relations, ensure_ascii=False, indent=4))
    else:
        print("⚠️  未识别出任何人物关系。")


if __name__ == "__main__":
    main()
