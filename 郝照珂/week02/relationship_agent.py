"""使用大模型结构化输出抽取人物关系。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator


load_dotenv(Path(__file__).with_name(".env"))


class Relationship(BaseModel):
    """人物关系图谱中的一条有向边。"""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="关系发起者")
    relation: str = Field(description="规范化后的人物关系")
    target: str = Field(description="关系指向者")

    @field_validator("source", "relation", "target")
    @classmethod
    def strip_and_require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("人物和关系字段不能为空")
        return value


class RelationshipResult(BaseModel):
    """模型结构化输出的顶层对象。"""

    model_config = ConfigDict(extra="forbid")
    relationships: list[Relationship] = Field(default_factory=list)


SYSTEM_PROMPT = """你是人物关系抽取智能体。请从用户文本中抽取所有明确表达的人物关系。

规则：
1. 每条关系必须包含 source、relation、target。
2. source 是关系发起者，target 是关系指向者；关系有方向，不能颠倒。
3. 提取全部关系，不要只提取第一条。
4. 不推测文本没有明确表达的事实；没有关系时返回空数组。
5. 合并完全重复的关系。
6. 统一常见关系名称：喜欢/暗恋/爱上→爱慕，讨厌/憎恨→厌恶，好友/好朋友→朋友。
7. 代词只有在指代明确时才能解析；不确定时保留原文称呼，不要编造姓名。
"""


RELATIONSHIP_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_relationships",
        "description": "提交从文本中抽取出的全部人物关系",
        "parameters": {
            "type": "object",
            "properties": {
                "relationships": {
                    "type": "array",
                    "description": "人物关系列表；没有明确关系时为空列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string", "description": "关系发起者"},
                            "relation": {"type": "string", "description": "规范化关系"},
                            "target": {"type": "string", "description": "关系指向者"},
                        },
                        "required": ["source", "relation", "target"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["relationships"],
            "additionalProperties": False,
        },
    },
}


def parse_json_payload(raw: str | dict[str, Any] | list[Any]) -> RelationshipResult:
    """解析模型返回值，并兼容代码块或直接数组形式。"""

    if isinstance(raw, dict):
        payload: Any = raw
    elif isinstance(raw, list):
        payload = {"relationships": raw}
    else:
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("模型没有返回可解析的 JSON")
            payload = json.loads(text[start : end + 1])

    if isinstance(payload, list):
        payload = {"relationships": payload}
    result = RelationshipResult.model_validate(payload)
    return RelationshipResult(relationships=_deduplicate(result.relationships))


def _deduplicate(items: list[Relationship]) -> list[Relationship]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[Relationship] = []
    for item in items:
        key = (item.source, item.relation, item.target)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


class RelationshipAgent:
    """支持 JSON Mode 和 Tool Calling 的人物关系抽取智能体。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.base_url = base_url or os.getenv("LLM_BASE_URL") or None
        self.model = model or os.getenv("LLM_MODEL", "deepseek-chat")

        if client is not None:
            self.client = client
        else:
            if not self.api_key:
                raise ValueError("未配置 LLM_API_KEY，请在 .env 或页面侧边栏中填写")
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def extract(
        self,
        text: str,
        mode: Literal["json", "tool"] = "json",
    ) -> RelationshipResult:
        text = text.strip()
        if not text:
            raise ValueError("请输入需要分析的文本")
        if mode == "json":
            return self._extract_with_json_mode(text)
        if mode == "tool":
            return self._extract_with_tool_call(text)
        raise ValueError(f"不支持的抽取模式：{mode}")

    def _messages(self, text: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]

    def _extract_with_json_mode(self, text: str) -> RelationshipResult:
        messages = self._messages(text)
        messages[0]["content"] += (
            '\n只返回 JSON 对象，格式为：'
            '{"relationships":[{"source":"人物A","relation":"关系","target":"人物B"}]}。'
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        return parse_json_payload(content)

    def _extract_with_tool_call(self, text: str) -> RelationshipResult:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self._messages(text),
            temperature=0,
            tools=[RELATIONSHIP_TOOL],
            tool_choice={
                "type": "function",
                "function": {"name": "submit_relationships"},
            },
        )
        message = response.choices[0].message
        tool_calls = message.tool_calls or []
        if tool_calls:
            return parse_json_payload(tool_calls[0].function.arguments)

        # 少数兼容接口可能忽略强制工具调用，回退解析正文。
        if message.content:
            return parse_json_payload(message.content)
        raise ValueError("模型既没有调用工具，也没有返回 JSON 内容")


def relationships_as_list(result: RelationshipResult) -> list[dict[str, str]]:
    """转换成题目要求的最外层数组格式。"""

    return [item.model_dump() for item in result.relationships]


if __name__ == "__main__":
    agent = RelationshipAgent()
    sample = "小明喜欢小白，但是小白喜欢小李，小李喜欢小明。"
    extracted = agent.extract(sample, mode="json")
    print(json.dumps(relationships_as_list(extracted), ensure_ascii=False, indent=2))
