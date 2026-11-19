"""OpenAI 兼容多模态 HTTP 客户端（基于 httpx）。"""

from __future__ import annotations

import base64
import json
import re

import httpx

from ..config import Settings


class VisionError(RuntimeError):
    """视觉调用相关错误。"""


def extract_json(text: str) -> dict:
    """从模型输出中提取第一个完整 JSON 对象，容忍围栏与多余尾随内容。"""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    if start == -1:
        raise VisionError("模型返回中未找到 JSON 对象")
    try:
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError as exc:
        raise VisionError(f"无法解析模型返回的 JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise VisionError("模型返回的 JSON 顶层不是对象")
    return obj


async def chat_vision(
    settings: Settings,
    *,
    system_prompt: str,
    user_prompt: str,
    image_bytes: bytes | None = None,
    mime: str = "image/jpeg",
) -> dict:
    """调用 OpenAI 兼容的多模态 chat 接口并返回解析后的 JSON。"""
    if not settings.llm_api_key or not settings.llm_base_url:
        raise VisionError("未配置 LLM_API_KEY / LLM_BASE_URL")

    content: list[dict] = [{"type": "text", "text": user_prompt}]
    if image_bytes is not None:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
        )

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0.1,
    }
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}

    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise VisionError(
                f"VLM 调用失败 {response.status_code}: {response.text[:300]}"
            )
        data = response.json()

    try:
        raw_text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionError("VLM 返回结构异常") from exc
    return extract_json(raw_text)
