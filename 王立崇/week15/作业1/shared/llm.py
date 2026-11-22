"""对 OpenAI 兼容接口的极简封装 + 容错 JSON 解析。

langgraph_app 的进程1 / 进程2 直接使用这里的方法；
agents_app 则使用 openai-agents SDK（见 agents_app/runtime_setup.py）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from .config import get_settings

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """每个进程各建一个客户端（懒加载）。"""
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.llm_enabled:
            raise RuntimeError("未配置 API Key，无法调用 LLM")
        _client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
            max_retries=1,
        )
    return _client


_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def extract_json(raw: str) -> dict[str, Any]:
    """从模型输出里尽量抠出一个 JSON 对象（处理 ```json 代码块、前后废话）。"""
    text = (raw or "").strip()
    candidates: list[str] = [text]

    match = _FENCE_RE.search(text)
    if match:
        candidates.append(match.group(1).strip())

    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    raise ValueError(f"无法从模型输出中解析 JSON：{raw!r}")


def complete_text(system: str, user: str, *, temperature: float = 0.7) -> str:
    settings = get_settings()
    response = get_client().chat.completions.create(
        model=settings.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()


def complete_json(system: str, user: str, *, temperature: float = 0.0) -> dict[str, Any]:
    settings = get_settings()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        response = get_client().chat.completions.create(
            model=settings.model,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
    except Exception:
        # 部分 OpenAI 兼容服务不支持 response_format，去掉后重试一次
        response = get_client().chat.completions.create(
            model=settings.model,
            messages=messages,
            temperature=temperature,
        )
    return extract_json(response.choices[0].message.content or "")
