# -*- coding: utf-8 -*-
"""多模态 LLM 客户端（OpenAI 兼容接口，默认接 DashScope 的 qwen-vl-plus）。

支持：
    - 文本 + 多张图片一起送入 LLM（图问一起问）
    - 把图片转 base64 内嵌到请求里（data URL），不依赖公网可访问的图片 URL
"""

import base64
import os

from openai import OpenAI

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME

_client = None


def _get_client():
    global _client
    if _client is None:
        if not LLM_API_KEY:
            raise RuntimeError("未配置 LLM_API_KEY，请在环境变量或 .env 中设置")
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return _client


def image_to_data_url(image_path):
    ext = os.path.splitext(image_path)[1].lstrip(".").lower() or "jpeg"
    if ext == "jpg":
        ext = "jpeg"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/{ext};base64,{b64}"


def build_user_content(question, context_text, image_paths, user_image_data_urls=None):
    """组装多模态 user message 的 content 数组。

    - context_text: RAG 检索到的文本上下文
    - image_paths:  RAG 检索到、与上下文配套的本地图片（会转 base64）
    - user_image_data_urls: 用户在提问时自己上传/附带的图片（已经是 data URL），用于"图问一起送"
    """
    content = []
    prompt_text = (
        "请基于以下资料回答用户问题。如果资料中有图片，可以结合图片内容作答。\n\n"
        f"【资料文本】\n{context_text}\n\n【用户问题】\n{question}"
    )
    content.append({"type": "text", "text": prompt_text})

    for path in image_paths:
        try:
            content.append({"type": "image_url", "image_url": {"url": image_to_data_url(path)}})
        except OSError:
            continue

    for data_url in (user_image_data_urls or []):
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    return content


def ask_multimodal(question, context_text, image_paths, user_image_data_urls=None):
    """调用多模态 LLM，返回回答文本。"""
    client = _get_client()
    content = build_user_content(question, context_text, image_paths, user_image_data_urls)
    response = client.chat.completions.create(
        model=LLM_MODEL_NAME,
        messages=[
            {"role": "system", "content": "你是一个严谨的文档问答助手，回答要基于给定资料，不要编造。"},
            {"role": "user", "content": content},
        ],
    )
    return response.choices[0].message.content
