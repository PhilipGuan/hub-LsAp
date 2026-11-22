"""文本处理小工具：压缩提示词长度、提取 JSON、读取最后一条用户消息。"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel


def compact_text(text: str, limit: int) -> str:
    """压平空白并截断，避免单条消息撑爆提示词。"""

    compact = " ".join(str(text).split())
    if limit <= 0 or len(compact) <= limit:
        return compact
    return compact[:limit] + "…"


def last_user_text(items: list[dict[str, Any]]) -> str:
    """从对话输入项中取最后一条 user 消息。"""

    for item in reversed(items):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            return "".join(parts).strip()
    return ""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_json_model(text: str, model_type: type[BaseModel]) -> BaseModel:
    """把模型输出解析成 Pydantic 模型。

    兼容三种常见情况：
    1. 纯 JSON；
    2. 包在 ```json 代码块里；
    3. JSON 前后夹杂了解释文字（截取第一个 ``{`` 到最后一个 ``}``）。
    """

    cleaned = _strip_code_fence(text or "")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as first_error:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(
                f"模型没有返回 JSON 对象：{compact_text(cleaned, 200)!r}"
            ) from first_error
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as second_error:
            raise ValueError(
                f"模型返回的 JSON 无法解析：{compact_text(cleaned, 200)!r}"
            ) from second_error

    if not isinstance(data, dict):
        raise ValueError(f"期望 JSON 对象，实际得到 {type(data).__name__}")
    return model_type.model_validate(data)
