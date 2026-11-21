# -*- coding: utf-8 -*-
"""大模型调用：文本检索结果 + 用户图片 + 检索图片。"""

from __future__ import annotations

import time
from pathlib import Path

from ..core.config import (
    CHAT_INCLUDE_RETRIEVED_IMAGES,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TIMEOUT,
    MAX_INLINE_IMAGES,
)
from .image_service import extract_image_refs, resolve_local_image, to_data_url

SYSTEM_PROMPT = "你是一个严谨的多模态文档问答助手，回答必须基于资料与用户图片。"

PROMPT = """请只依据【资料】和用户提供的图片回答问题。

【问题】
{question}

【资料】
{context}

【要求】
1. 只使用资料与图片中的信息；没有依据时回答“资料中未提及”，禁止编造。
2. 回答末尾用「来源：文件名 第N页」标注依据，多个来源分行列出。
3. 资料中的图片引用形如 ![说明](路径)，如果与回答相关，请原样保留在对应段落，不要改写路径。
4. 如果用户提供了图片，请结合图片内容回答，并明确区分“图片中”和“资料中”。
5. 回答简洁、分点，不要复述资料原文。"""


def check_llm() -> tuple[bool, str]:
    if not LLM_API_KEY:
        return False, "backend/.env 未配置 OPENAI_API_KEY"
    return True, f"模型 {LLM_MODEL} @ {LLM_BASE_URL}"


def _collect_retrieved_images(hits: list[dict], limit: int) -> list[Path]:
    images: list[Path] = []
    seen: set[str] = set()
    for hit in hits:
        for ref in extract_image_refs(hit.get("text") or ""):
            real = resolve_local_image(ref)
            if real is None:
                continue
            key = str(real)
            if key in seen:
                continue
            seen.add(key)
            images.append(real)
            if len(images) >= limit:
                return images
    return images


def answer(
    question: str,
    hits: list[dict],
    user_image: Path | None = None,
) -> tuple[str, int]:
    if not LLM_API_KEY:
        raise RuntimeError("backend/.env 未配置 OPENAI_API_KEY")

    from openai import OpenAI

    context_parts = []
    for hit in hits:
        page = hit.get("page") or 0
        page_text = f"第{page}页" if page else "页码未知"
        context_parts.append(
            f"[来源：{hit.get('file_name') or '未知文件'} {page_text}]\n{hit.get('text') or ''}"
        )
    context = "\n\n---\n\n".join(context_parts) or "（没有检索到资料）"

    content: list[dict] = [{"type": "text", "text": PROMPT.format(question=question, context=context)}]

    if user_image is not None and Path(user_image).is_file():
        content.append({"type": "text", "text": "【用户提供的图片】"})
        content.append({"type": "image_url", "image_url": {"url": to_data_url(user_image)}})

    if CHAT_INCLUDE_RETRIEVED_IMAGES:
        for image in _collect_retrieved_images(hits, MAX_INLINE_IMAGES):
            content.append({"type": "text", "text": f"【检索到的图片：{image.name}】"})
            content.append({"type": "image_url", "image_url": {"url": to_data_url(image)}})

    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT)
    started = time.time()
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        temperature=0.2,
        max_tokens=1200,
    )
    elapsed_ms = int((time.time() - started) * 1000)

    if not response.choices:
        raise RuntimeError("大模型没有返回候选结果")
    return response.choices[0].message.content or "", elapsed_ms
