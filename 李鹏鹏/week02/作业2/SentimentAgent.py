import json
from typing import Any

from openai import OpenAI


class SentimentAgent(object):

    def __init__(self):
        self.relationships: list[dict] = list()

        self.model_name = "deepseek-v4-flash"
        self.base_url = "https://api.deepseek.com"
        self.api_key = "sk-e16dfcxxxx22f96c4a8f"

        self.ai_client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "save_relationship_graph",
                    "description": (
                        "将文本中提取出的人物情感关系三元组（source-relation-target）"
                        "保存为结构化图谱。仅在模型完成全部抽取后调用一次。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "relationships": {
                                "type": "array",
                                "description": "人物关系列表，每条是一个三元组",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "source": {
                                            "type": "string",
                                            "description": "关系主体（谁发出情感/动作）",
                                        },
                                        "relation": {
                                            "type": "string",
                                            "description": "情感或关系类型，如：爱慕、喜欢、讨厌、朋友、敌对",
                                        },
                                        "target": {
                                            "type": "string",
                                            "description": "关系客体（情感或动作的承受者）",
                                        },
                                    },
                                    "required": ["source", "relation", "target"],
                                },
                            },
                        },
                        "required": ["relationships"],
                    },
                },
            }
        ]

    @staticmethod
    def save_relationship_graph(self, relationships: list[dict]) -> str:
        """保存并返回人物关系图谱。"""
        print(f"\n    📊  解析得到 {len(relationships)} 条人物关系：")
        for i, r in enumerate(relationships, 1):
            print(f"      {i}. {r.get('source')}  --[{r.get('relation')}]-->  {r.get('target')}")
        return json.dumps(relationships, ensure_ascii=False)

    def run_tool_call(self, toolcall: Any) -> str:
        """执行一次工具调用，返回结果字符串。"""
        name = toolcall.function.name
        args_value = json.loads(toolcall.function.arguments)
        print(f"    → 调用工具: {name}({json.dumps(args_value, ensure_ascii=False)})")
        result_value = self.save_relationship_graph(self, **args_value)
        print(f"    ← 结果: {result_value}")
        return result_value

    def get_result(self):
        messages = [
            {"role": "system",
             "content": "你是人物关系分析助手，负责从用户的中文文本中识别'人物'与'人物之间的情感/关系'"},
            {"role": "user", "content": "小明喜欢小姚，但是小姚喜欢小王。"}
        ]

        # noinspection PyTypeChecker
        response = self.ai_client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            tools=self.tools,
            temperature=0.0,
            extra_body={"thinking": {"type": "disabled"}}
        )

        # noinspection PyUnresolvedReferences
        msg = response.choices[0].message

        # 模型可能直接回复（不需要工具），也可能发起工具调用
        if msg.tool_calls:
            for tc in msg.tool_calls:
                result = self.run_tool_call(tc)
                # noinspection PyUnresolvedReferences
                args = json.loads(tc.function.arguments)
                self.relationships = args.get("relationships", list())
                # noinspection PyTypeChecker
                messages.append(msg)  # 保留 assistant 的 tool_calls
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            # 把工具结果发回模型，让其生成最终回复
            # noinspection PyTypeChecker
            final = self.ai_client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                tools=self.tools,
                extra_body={"thinking": {"type": "disabled"}},
                temperature=0.0,
            )
            # noinspection PyUnresolvedReferences
            print(f"\n最终回复: \n {final.choices[0].message.content}")
        else:
            print(f"直接回复: {msg.content}")

        print(f"\n人物关系图：\n {json.dumps(self.relationships, ensure_ascii=False, indent=4)}")


if __name__ == '__main__':
    agent = SentimentAgent()
    agent.get_result()
