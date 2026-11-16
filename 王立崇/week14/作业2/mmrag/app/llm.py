"""OpenAI-compatible multimodal LLM calls (local vLLM / Qwen by default)."""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from PIL import Image, ImageOps

from .config import (
    CAPTION_MAX_EDGE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
    LLM_VISION_MODEL,
    MAX_CONTEXT_CHARS,
    MAX_LLM_HISTORY_TURNS,
    MAX_VISION_CONTEXTS,
    PARSED_DIR,
)

logger = logging.getLogger(__name__)

client = AsyncOpenAI(
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
    timeout=LLM_TIMEOUT_SECONDS,
    max_retries=1,
)


class LLMError(RuntimeError):
    pass


def image_data_url(path: Path, *, max_edge: int = 1280, quality: int = 84) -> str:
    """Downscale a local image and return a JPEG data URL."""
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in {"RGB", "L"}:
            background = Image.new("RGB", image.size, "white")
            if image.mode in {"RGBA", "LA"}:
                background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
            else:
                background.paste(image.convert("RGB"))
            image = background
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _clean_answer(value: str) -> str:
    text = (value or "").strip()
    for marker in ("<|im_end|>", "<|endoftext|>"):
        text = text.replace(marker, "")
    return text.strip()


def _content_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                pieces.append(str(part.get("text", "")))
            else:
                pieces.append(str(getattr(part, "text", "")))
        return "".join(pieces)
    return str(content or "")


async def caption_image(
    path: Path, *, document_title: str = "", heading: str = ""
) -> str:
    """Ask the vision model for a retrieval-friendly description of a figure."""
    context = []
    if document_title:
        context.append(f"文档标题：{document_title}")
    if heading:
        context.append(f"所属章节：{heading}")
    context_block = "\n".join(context)
    prompt = (
        "你是文档图像理解助手。请用中文客观描述这张文档插图，提取图表标题、"
        "关键文字/数字、流程或结构关系。不要虚构看不见的内容，不要寒暄，"
        "80 字以内。\n" + context_block
    )
    try:
        data_url = image_data_url(path, max_edge=CAPTION_MAX_EDGE)
        response = await client.chat.completions.create(
            model=LLM_VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            temperature=0.1,
            max_tokens=220,
        )
        text = _clean_answer(_content_text(response.choices[0].message))
        text = text.replace("图片描述：", "").strip().strip('"“”')
        return text[:300]
    except Exception as exc:  # captioning must not fail the whole ingestion
        logger.warning("caption failed for %s: %s", path, exc)
        return ""


async def describe_query_image(data_url: str, question: str) -> str:
    """Use the image as extra retrieval evidence, not just as an LLM attachment."""
    try:
        response = await client.chat.completions.create(
            model=LLM_VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {
                            "type": "text",
                            "text": (
                                "请提取这张图片中的文字、图表标题和关键信息，作为文档检索线索。"
                                f"用户的问题：{question}\n只输出一段 100 字以内的客观描述。"
                            ),
                        },
                    ],
                }
            ],
            temperature=0.1,
            max_tokens=220,
        )
        return _clean_answer(_content_text(response.choices[0].message))[:500]
    except Exception as exc:
        logger.warning("query image description failed: %s", exc)
        return ""


def _context_prompt(references: list[dict[str, Any]]) -> str:
    lines = [
        "【检索资料】",
        "下面每一段资料都有编号。文字资料直接给出；图片资料附带图片描述和图片链接。",
    ]
    used = len("\n".join(lines))
    for ref in references:
        heading = f" · {ref['heading']}" if ref.get("heading") else ""
        image_line = ""
        if ref.get("image_path"):
            image_line = f"\n图片链接：{ref['image_url']}"
        block = (
            f"\n[{ref['n']}]《{ref['doc_name']}》第 {ref['page']} 页"
            f"{heading} · {ref.get('kind_label', ref['kind'])}"
            f"\n{ref['content']}{image_line}"
        )
        if used + len(block) > MAX_CONTEXT_CHARS:
            break
        lines.append(block)
        used += len(block)
    if len(lines) == 2:
        lines.append("\n（没有检索到相关资料。）")
    return "\n".join(lines)


async def answer_question(
    *,
    question: str,
    references: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
    user_image_data_url: str | None = None,
    query_image_description: str = "",
) -> str:
    system_prompt = (
        "你是「多模态文档问答助手」。你只能依据【检索资料】和用户上传的图片作答，"
        "资料可能包含正文、表格和插图。\n\n"
        "回答规则：\n"
        "1. 用中文回答，结构清晰，使用 Markdown；必要时使用 Markdown 表格。\n"
        "2. 每个来自检索资料的关键结论后面标注引用编号，例如 [1]、[2]。\n"
        "3. 你还会直接看到若干张“资料配图”。如果图片确实有助于回答，请在正文合适位置"
        "插入 Markdown 图片，图片链接必须原样使用资料或配图说明中给出的本地链接，"
        "绝不能编造 URL。图片下方的图注写明文档名和页码。\n"
        "4. 如果用户上传了图片，先判断图片内容，再结合检索资料回答；看不到的细节不要猜。\n"
        "5. 如果资料不足，明确说“检索资料中没有足够信息”，并指出还缺什么；"
        "不要用常识冒充文档内容。\n"
        "6. 不要输出你的思考过程，不要重复【检索资料】原文，直接给答案。"
    )

    context_text = _context_prompt(references)
    visual_refs = [ref for ref in references if ref.get("image_path")][:MAX_VISION_CONTEXTS]
    content: list[dict[str, Any]] = [{"type": "text", "text": context_text}]

    if visual_refs:
        content.append(
            {
                "type": "text",
                "text": "下面是检索到的资料配图，按编号顺序提供：",
            }
        )
    for ref in visual_refs:
        path = PARSED_DIR / ref["image_path"]
        if not path.is_file():
            continue
        try:
            url = image_data_url(path, max_edge=1280, quality=82)
        except Exception as exc:
            logger.warning("cannot attach context image %s: %s", path, exc)
            continue
        content.append(
            {
                "type": "text",
                "text": (
                    f"资料 [{ref['n']}] 配图（《{ref['doc_name']}》第 {ref['page']} 页，"
                    f"本地链接 {ref['image_url']}）："
                ),
            }
        )
        content.append({"type": "image_url", "image_url": {"url": url}})

    if user_image_data_url:
        content.append({"type": "text", "text": "下面是用户本次提问上传的图片："})
        content.append({"type": "image_url", "image_url": {"url": user_image_data_url}})

    extra = ""
    if query_image_description:
        extra = f"\n（系统对用户上传图片的初步识别：{query_image_description}）"
    content.append(
        {
            "type": "text",
            "text": f"【用户问题】\n{question}{extra}\n\n请直接作答。",
        }
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn in (history or [])[-MAX_LLM_HISTORY_TURNS * 2 :]:
        role = turn.get("role")
        text = (turn.get("content") or "").strip()
        if role in {"user", "assistant"} and text:
            messages.append({"role": role, "content": text[:2000]})
    messages.append({"role": "user", "content": content})

    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.2,
            top_p=0.85,
            max_tokens=LLM_MAX_TOKENS,
        )
        text = _clean_answer(_content_text(response.choices[0].message))
        if not text:
            raise LLMError("模型返回了空答案")
        if response.usage:
            logger.info(
                "llm usage prompt=%s completion=%s",
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
        return text
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"多模态模型调用失败：{exc}") from exc


async def llm_health() -> tuple[bool, str]:
    try:
        models = await client.models.list()
        names = [model.id for model in models.data]
        return True, names[0] if names else LLM_MODEL
    except Exception as exc:
        return False, str(exc)
