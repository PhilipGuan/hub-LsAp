"""
多模态 RAG 问答页。

能力：
1. 文本提问 -> bge 文本检索 + CLIP 文本检索（文本查图）
2. 图片提问 -> CLIP 图片检索（以图搜图），同时把图片送给 Qwen-VL
3. 图文混排输出 -> 检索到的图片会随回答一起渲染
"""
import uuid
from pathlib import Path

import streamlit as st
from openai import OpenAI

from config import (
    CHAT_UPLOAD_DIR,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TEMPERATURE,
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_IMAGES,
    MAX_USER_IMAGES,
    PROCESSED_DIR,
    TOP_K,
)
from embeddings import encode_clip_image, encode_clip_text, encode_text
from text_utils import (
    extract_image_urls,
    file_to_data_url,
    resolve_image_path,
    rewrite_relative_images,
)
from ui_common import render_markdown_with_images, render_sidebar
from vector_store import search

SYSTEM_PROMPT = "你是一个严谨、乐于解释的多模态知识库问答助手。"

RAG_PROMPT = """你是多模态知识库问答助手，请严格依据检索资料回答用户的问题。

【检索资料】
{context}

【用户问题】
{question}

【回答要求】
1. 只使用检索资料中的信息回答；资料不足时明确说明“资料中没有相关信息”，不要编造。
2. 回答要图文混排：当资料中的图片有助于说明问题时，把图片 markdown 单独放在一行，
   并原样保留资料中给出的图片路径，例如：
   ![图片说明](/absolute/path/to/image.jpg)
   不要改写、缩写或拼接图片路径。
3. 如果用户上传了图片，需要结合图片内容进行回答。
4. 使用中文回答，条理清晰；末尾用「来源：文件名」标注使用的资料。
"""


# ---------------------------------------------------------------------------
# 会话状态 / 工具函数
# ---------------------------------------------------------------------------
def _init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []


def _save_chat_image(uploaded_file) -> str:
    suffix = Path(uploaded_file.name).suffix.lower() or ".png"
    destination = CHAT_UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    destination.write_bytes(uploaded_file.getvalue())
    return str(destination)


@st.cache_resource
def _llm_client() -> OpenAI:
    return OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)


def _collect_hits(question: str, image_paths):
    """文本检索 + CLIP 文本检索 + CLIP 图片检索，按 id 去重后取分数最高者。"""
    raw_hits = []

    if question.strip():
        raw_hits.extend(search("text_vector", encode_text(question), TOP_K))
        raw_hits.extend(search("clip_text_vector", encode_clip_text(question), TOP_K))

    for image_path in image_paths:
        raw_hits.extend(
            search("clip_image_vector", encode_clip_image(image_path), TOP_K)
        )

    merged = {}
    for hit in raw_hits:
        hit_id = hit.get("id")
        score = float(hit.get("distance", 0.0))
        entity = hit.get("entity", {}) or {}
        current = merged.get(hit_id)
        if current is None or score > current["score"]:
            merged[hit_id] = {"score": score, "entity": entity}

    ranked = sorted(merged.values(), key=lambda item: item["score"], reverse=True)
    return ranked[: max(TOP_K * 2, TOP_K)]


def _build_context(hits):
    """
    把检索结果拼成 prompt 上下文。
    同时收集：图片所在目录（供前端解析相对路径）、可用于多模态推理的图片。
    """
    blocks = []
    search_dirs = []
    context_images = []
    total_chars = 0

    for index, hit in enumerate(hits, start=1):
        entity = hit["entity"]
        image_dir = entity.get("image_dir") or ""
        text = rewrite_relative_images(entity.get("text", ""), image_dir)

        block = (
            f"[资料{index}] 来源：{entity.get('file_name', '未知文件')}"
            f"（第 {int(entity.get('chunk_index', 0)) + 1} 段）\n{text}"
        )
        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - total_chars
            if remaining > 200:
                blocks.append(block[:remaining])
            break

        blocks.append(block)
        total_chars += len(block)

        if image_dir and image_dir not in search_dirs:
            search_dirs.append(image_dir)

        for url in extract_image_urls(text):
            resolved = resolve_image_path(url, [image_dir])
            if (
                resolved
                and not resolved.startswith(("http://", "https://", "data:"))
                and Path(resolved).is_file()
                and resolved not in context_images
                and len(context_images) < MAX_CONTEXT_IMAGES
            ):
                context_images.append(resolved)

    if not search_dirs:
        search_dirs.append(str(PROCESSED_DIR))

    return "\n\n".join(blocks), search_dirs, context_images


def _image_part(path: str) -> dict:
    return {"type": "image_url", "image_url": {"url": file_to_data_url(path)}}


def _build_llm_messages(question, context, user_images, context_images):
    """构造 OpenAI 兼容的多模态 messages：文本 + 用户图片 + 检索图片。"""
    content = []

    # 1) 用户上传的图片
    for index, path in enumerate(user_images, start=1):
        content.append({"type": "text", "text": f"【用户上传的图片 {index}】"})
        content.append(_image_part(path))

    # 2) 检索资料 + 问题 + 回答要求
    content.append(
        {
            "type": "text",
            "text": RAG_PROMPT.format(
                context=context or "（本次没有检索到资料）",
                question=question or "请结合图片内容进行说明。",
            ),
        }
    )

    # 3) 检索到的图片（连同路径一起给模型，方便它原样引用）
    for index, path in enumerate(context_images, start=1):
        content.append(
            {
                "type": "text",
                "text": f"【检索到的资料图片 {index}，引用时请原样使用路径：{path}】",
            }
        )
        content.append(_image_part(path))

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _call_llm(messages) -> str:
    response = _llm_client().chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=LLM_TEMPERATURE,
    )
    return (response.choices[0].message.content or "").strip()


def _summarize_sources(hits):
    return [
        {
            "file_name": hit["entity"].get("file_name", "未知文件"),
            "chunk_index": int(hit["entity"].get("chunk_index", 0)),
            "score": hit["score"],
        }
        for hit in hits
    ]


def _render_sources(sources) -> None:
    if not sources:
        return
    with st.expander(f"查看检索来源（{len(sources)} 条）"):
        for source in sources:
            st.markdown(
                f"- **{source['file_name']}** · 第 {source['chunk_index'] + 1} 段 · "
                f"相似度 `{source['score']:.3f}`"
            )


def _render_message(message) -> None:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            render_markdown_with_images(
                message.get("content", ""), message.get("search_dirs")
            )
            _render_sources(message.get("sources", []))
            return

        if message.get("content"):
            st.markdown(message["content"])
        for image_path in message.get("images", []):
            st.image(image_path, width="stretch")


def _answer(question, user_images):
    hits = _collect_hits(question, user_images)
    context, search_dirs, context_images = _build_context(hits)
    messages = _build_llm_messages(question, context, user_images, context_images)
    answer = _call_llm(messages)
    answer = _ensure_image_mix(answer, context_images)
    return answer, search_dirs, _summarize_sources(hits)


def _ensure_image_mix(answer: str, context_images) -> str:
    """
    兜底保证“图文混排”：如果模型只输出了文字，而检索结果里有相关图片，
    就把最相关的图片附在回答末尾，避免用户完全看不到图。
    """
    if not context_images or extract_image_urls(answer):
        return answer
    lines = "\n\n**相关图片：**\n" + "\n".join(
        f"![相关图片]({path})" for path in context_images[:2]
    )
    return answer + lines


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
_init_state()
render_sidebar()

with st.sidebar:
    if st.button("清空当前对话", width="stretch"):
        st.session_state.messages = []
        st.rerun()

st.title("💬 多模态图文问答")
st.caption(
    "可以只输入文字，也可以“文字 + 图片”一起提问。"
    "系统会检索知识库中的文本和图片，并用 Qwen-VL 生成图文混排的回答。"
)

if not LLM_API_KEY:
    st.warning(
        "未检测到 `LLM_API_KEY` / `DASHSCOPE_API_KEY`，问答功能将不可用。"
        "请在 `.env` 或环境变量中配置 DashScope API Key。"
    )

for message in st.session_state.messages:
    _render_message(message)

prompt = st.chat_input(
    "输入问题，可同时上传图片",
    accept_file="multiple",
    file_type=["png", "jpg", "jpeg", "webp", "bmp"],
)

if prompt is not None:
    question = (prompt.text or "").strip()
    uploads = list(prompt.files or [])[:MAX_USER_IMAGES]

    if not question and not uploads:
        st.warning("请输入问题或上传图片。")
    else:
        user_images = [_save_chat_image(item) for item in uploads]

        with st.chat_message("user"):
            if question:
                st.markdown(question)
            for image_path in user_images:
                st.image(image_path, width="stretch")
        st.session_state.messages.append(
            {"role": "user", "content": question, "images": user_images}
        )

        with st.chat_message("assistant"):
            with st.spinner("正在检索资料并生成回答 ..."):
                try:
                    answer, search_dirs, sources = _answer(question, user_images)
                except Exception as exc:
                    answer = f"抱歉，生成回答失败：{exc}"
                    search_dirs = []
                    sources = []

            render_markdown_with_images(answer, search_dirs)
            _render_sources(sources)

        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": answer,
                "search_dirs": search_dirs,
                "sources": sources,
            }
        )
