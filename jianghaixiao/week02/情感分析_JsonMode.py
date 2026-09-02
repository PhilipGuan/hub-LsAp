"""
JSON Mode 版 — 人物关系情感分析智能体

原理：
  设置 response_format={"type": "json_object"}，让模型直接输出
  符合要求的 JSON 结构，程序解析后展示关系图谱。

  相比 Tool Call 版，JSON Mode 更简洁——一次调用即可拿到全部结果，
  不依赖多次 tool_call 交互。

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
    api_key="sk-e16dfcaa8c7exxxabud922f96c4a8f",
    base_url="https://api.deepseek.com",
)


# ════════════════════════════════════════════════════════════════════════
# 1. 情感词归一化映射（与 Tool Call 版保持一致）
# ════════════════════════════════════════════════════════════════════════

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
# 2. 安全 JSON 解析（处理空 content 和格式异常）
# ════════════════════════════════════════════════════════════════════════

def safe_json_parse(text: str) -> dict | list | None:
    """安全解析 JSON，兼容外层包裹对象和直接数组。"""
    if not text or not text.strip():
        print("    ⚠️  模型返回了空内容（JSON 模式偶发问题）")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"    ⚠️  JSON 解析失败: {e}")
        # 尝试清理 markdown 代码块标记
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.splitlines()[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.splitlines()[:-1])
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            print(f"    原始内容预览: {text[:300]}")
            return None


# ════════════════════════════════════════════════════════════════════════
# 3. 核心：通过 JSON Mode 提取关系
# ════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是一个人物关系情感分析专家。

你的任务是从用户输入的文本中，提取所有人物之间的情感关系，并以 JSON 格式输出。

输出格式要求：
- 顶层为对象，包含一个 "relationships" 字段，值为关系数组
- 每个关系对象包含三个字段：
    - "source":  情感发出方（人名）
    - "relation": 情感关系类型
    - "target":  情感接收方（人名）

情感关系类型使用以下标准词之一：爱慕、暗恋、讨厌、憎恨、关心
请根据文本情感强烈程度选择合适的词。

输出示例：
{
    "relationships": [
        {"source": "小明", "relation": "爱慕", "target": "小姚"},
        {"source": "小姚", "relation": "爱慕", "target": "小王"}
    ]
}

如果文本中没有识别出任何人物关系，请返回空数组：
{"relationships": []}
"""


def analyze_relationships(text: str) -> list[dict]:
    """单次调用 LLM + JSON Mode，提取所有人物关系。"""

    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        max_tokens=1024,
        temperature=0.1,
    )

    content = response.choices[0].message.content
    result = safe_json_parse(content)

    if result is None:
        return []

    # 兼容两种格式：直接数组 or 对象含 relationships 字段
    if isinstance(result, list):
        raw_list = result
    else:
        raw_list = result.get("relationships", result.get("relations", []))

    # 归一化并清理
    relationships = []
    for item in raw_list:
        source = item.get("source", "").strip()
        relation = normalize_relation(item.get("relation", "").strip())
        target = item.get("target", "").strip()
        if source and relation and target:
            relationships.append({
                "source": source,
                "relation": relation,
                "target": target,
            })

    return relationships


# ════════════════════════════════════════════════════════════════════════
# 4. 主程序入口
# ════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 55)
    print("  人物关系情感分析智能体（JSON Mode 版）")
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
