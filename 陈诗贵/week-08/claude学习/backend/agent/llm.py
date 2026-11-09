"""DeepSeek LLM 封装（OpenAI 兼容接口）。"""
from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from .. import config

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
        )
    return _client


def _ensure_key() -> None:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError(
            "未设置 DEEPSEEK_API_KEY。请先：export DEEPSEEK_API_KEY=sk-xxx "
            "（或写入 backend/.env）"
        )


async def chat(messages: list[dict], temperature: float = 0.2) -> str:
    """发起一次对话，返回助手文本回复。"""
    _ensure_key()
    client = _get_client()
    resp = await client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=messages,
        temperature=temperature,
    )
    content = resp.choices[0].message.content or ""
    return content.strip()


async def chat_json(messages: list[dict], temperature: float = 0.2) -> dict:
    """发起一次对话，返回解析后的 JSON 对象（要求模型只输出 JSON）。"""
    _ensure_key()
    client = _get_client()
    resp = await client.chat.completions.create(
        model=config.DEEPSEEK_MODEL,
        messages=messages,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or ""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("LLM 返回的不是合法 JSON：%s\n原文：%s", exc, content[:500])
        raise
