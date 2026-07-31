"""
作业二：基于 LLM Tool Call 的简单人物关系情感分析智能体

示例输入：小明喜欢小姚，但是小姚喜欢小王。
示例输出：人物关系图谱 JSON 列表
"""

import json
from pathlib import Path

from openai import OpenAI

# zhangzhiyun/apikey/deepseek.json
APIKEY_DIR = Path(__file__).resolve().parents[2] / "apikey"
CONFIG_PATH = APIKEY_DIR / "deepseek.json"


def load_deepseek_config() -> dict:
    """从 zhangzhiyun/apikey/deepseek.json 读取 DeepSeek 配置。"""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"未找到配置文件：{CONFIG_PATH}\n"
            f"请创建该文件并填写真实 api_key。"
        )
    with CONFIG_PATH.open(encoding="utf-8") as f:
        config = json.load(f)

    api_key = (config.get("api_key") or "").strip()
    if not api_key or api_key.startswith("在此填写"):
        raise ValueError(f"请先在 {CONFIG_PATH} 中填写真实的 DeepSeek api_key")

    return {
        "api_key": api_key,
        "base_url": config.get("base_url", "https://api.deepseek.com"),
        "model": config.get("model", "deepseek-chat"),
    }


_config = load_deepseek_config()
client = OpenAI(api_key=_config["api_key"], base_url=_config["base_url"])
MODEL = _config["model"]

# 定义 tool：让模型把抽取到的关系填进结构化参数
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "build_relation_graph",
            "description": "根据文本构建人物情感关系图谱，仅抽取人物之间的情感态度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "relations": {
                        "type": "array",
                        "description": "人物情感关系列表（仅含情感，不含社会/身份关系）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {
                                    "type": "string",
                                    "description": "情感发出方（人物名）",
                                },
                                "relation": {
                                    "type": "string",
                                    "description": "情感关系，仅限情感词，如：爱慕、喜欢、暗恋、讨厌、嫉妒、尊敬、厌恶",
                                },
                                "target": {
                                    "type": "string",
                                    "description": "情感接收方（人物名）",
                                },
                            },
                            "required": ["source", "relation", "target"],
                        },
                    }
                },
                "required": ["relations"],
            },
        },
    }
]


EMPTY_RELATION_MSG = "无法获取人物情感关系"


def format_relations(relations: list[dict]):
    """有情感关系则返回列表，否则返回提示文案。"""
    if relations:
        return relations
    return EMPTY_RELATION_MSG


def analyze_relations(text: str) -> list[dict]:
    """调用 LLM tool call，从自然语言中抽取人物情感关系图谱。"""
    print("-" * 50)
    print(f"[1/4] 接收输入：{text}")
    print(f"[2/4] 调用模型：{MODEL}（thinking=off, temperature=0）")
    print("      请求工具：build_relation_graph")

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是人物情感关系分析助手。"
                    "请阅读用户文本，只抽取人物之间的情感态度，"
                    "并调用 build_relation_graph 工具输出结果。"
                    "relation 只能是情感词，例如：爱慕、喜欢、暗恋、讨厌、嫉妒、尊敬、厌恶。"
                    "不要输出社会或身份关系，例如：朋友、家人、父子、兄妹、同事、追求、拒绝。"
                    "若某段描述只有身份/社会关系而无情感（如“是好朋友”），则返回空的 relations 列表。"
                ),
            },
            {"role": "user", "content": text},
        ],
        tools=TOOLS,
        tool_choice={"type": "function", "function": {"name": "build_relation_graph"}},
        temperature=0,
        max_tokens=256,
        # 关闭 thinking，走非推理路径，更快更省
        extra_body={"thinking": {"type": "disabled"}},
    )

    choice = response.choices[0]
    message = choice.message
    usage = response.usage
    print(f"[3/4] 模型返回：finish_reason={choice.finish_reason}")
    if usage:
        print(
            f"      token 用量：prompt={usage.prompt_tokens}, "
            f"completion={usage.completion_tokens}, total={usage.total_tokens}"
        )

    if not message.tool_calls:
        raise RuntimeError("模型未发起 tool call，请检查 API / 模型是否支持 function calling")

    for call in message.tool_calls:
        print(f"      tool_call：{call.function.name}")
        print(f"      原始参数：{call.function.arguments}")
        if call.function.name == "build_relation_graph":
            args = json.loads(call.function.arguments)
            relations = args.get("relations", [])
            print("[4/4] 解析完成：人物情感关系图谱")
            print(json.dumps(format_relations(relations), ensure_ascii=False, indent=2))
            print("-" * 50)
            return relations

    raise RuntimeError(f"未找到 build_relation_graph 调用，实际调用：{[c.function.name for c in message.tool_calls]}")


def main() -> None:
    cases = [
        "小明喜欢小姚，但是小姚喜欢小王。",
        "张三和李四是好朋友，王五讨厌张三。",
        "小红暗恋小刚，小刚只把小红当妹妹。",
        "老王是小王的父亲，小王很尊敬老王。",
        "阿强追求阿美，阿美拒绝了阿强，阿美喜欢阿伟。",
        "阿猫和阿狗是好朋友",
    ]

    print("=" * 50)
    print(f"开始运行内置用例，共 {len(cases)} 条")
    print("=" * 50)

    results = []
    for i, text in enumerate(cases, start=1):
        print(f"\n>>> 用例 {i}/{len(cases)}")
        relations = analyze_relations(text)
        results.append({"input": text, "relations": format_relations(relations)})

    print("\n全部用例结果汇总：")
    print(json.dumps(results, ensure_ascii=False, indent=2))

    print("\n进入交互模式，输入一段文本进行情感关系分析。")
    print("直接回车或输入 q / quit / exit 退出。\n")
    while True:
        try:
            text = input("请输入文本> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if not text or text.lower() in {"q", "quit", "exit"}:
            print("已退出。")
            break

        analyze_relations(text)
        print()


if __name__ == "__main__":
    main()


"""
输出效果：
[
  {
    "input": "小明喜欢小姚，但是小姚喜欢小王。",
    "relations": [
      {
        "source": "小明",
        "relation": "喜欢",
        "target": "小姚"
      },
      {
        "source": "小姚",
        "relation": "喜欢",
        "target": "小王"
      }
    ]
  },
  {
    "input": "张三和李四是好朋友，王五讨厌张三。",
    "relations": [
      {
        "source": "王五",
        "relation": "讨厌",
        "target": "张三"
      }
    ]
  },
  {
    "input": "小红暗恋小刚，小刚只把小红当妹妹。",
    "relations": [
      {
        "source": "小红",
        "relation": "暗恋",
        "target": "小刚"
      },
      {
        "source": "小刚",
        "relation": "喜欢",
        "target": "小红"
      }
    ]
  },
  {
    "input": "老王是小王的父亲，小王很尊敬老王。",
    "relations": [
      {
        "source": "小王",
        "relation": "尊敬",
        "target": "老王"
      }
    ]
  },
  {
    "input": "阿强追求阿美，阿美拒绝了阿强，阿美喜欢阿伟。",
    "relations": [
      {
        "source": "阿强",
        "relation": "喜欢",
        "target": "阿美"
      },
      {
        "source": "阿美",
        "relation": "喜欢",
        "target": "阿伟"
      }
    ]
  },
  {
    "input": "阿猫和阿狗是好朋友",
    "relations": "无法获取人物情感关系"
  }
]
"""