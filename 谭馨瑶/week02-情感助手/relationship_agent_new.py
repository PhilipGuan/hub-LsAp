from pydantic import BaseModel, Field
from typing import List
from typing_extensions import Literal

class RelationshipAgent:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def call(self, user_prompt, response_model):
        messages = [
            {
                "role": "user",
                "content": user_prompt
            }
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": response_model.schema()['title'],
                    "description": response_model.schema()['description'],
                    "parameters": response_model.model_json_schema(),
                    },
                }
        ]

        response = client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        try:
            arguments = response.choices[0].message.tool_calls[0].function.arguments
            return response_model.model_validate_json(arguments)
        except:
            print('ERROR', response.choices[0].message)
            return None

class Relationship(BaseModel):
    """一条人物关系"""
    source: str = Field(description="关系发起者")
    relation: Literal["爱慕", "反感", "未知"] = Field(
        description="喜欢、暗恋归为爱慕；不喜欢、讨厌归为反感；无法判断归为未知"
    )
    target: str = Field(description="关系指向的人物")


class Text(BaseModel):
    """抽取文本中的全部人物关系"""
    relationships: List[Relationship] = Field(description="人物关系列表")

import os
import json
from openai import OpenAI
client = OpenAI(
    api_key="sk-", 
    base_url="https://api.siliconflow.cn/v1",
)

result =RelationshipAgent(model_name = 'deepseek-ai/DeepSeek-V4-Flash').call('小明喜欢小姚，但是小姚喜欢小王。', Text)

print(json.dumps(result.model_dump()["relationships"], ensure_ascii=False, indent=2))
