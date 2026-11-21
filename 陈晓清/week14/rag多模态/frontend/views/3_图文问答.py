# -*- coding: utf-8 -*-
"""图文问答页：文字提问 / 图片 + 文字提问 / 图文混排回答。"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

import api_client as api

IMAGE_RE = re.compile(r"!\[(.*?)\]\((.*?)\)")


def render_answer(text: str) -> None:
    """把回答中的图片引用拆出来，用 st.image 渲染（st.markdown 不显示本地图片）。"""
    last = 0
    for match in IMAGE_RE.finditer(text or ""):
        before = text[last : match.start()]
        if before.strip():
            st.markdown(before)
        try:
            st.image(api.image_url(match.group(2).strip()), use_container_width=True)
        except Exception:
            st.caption(f"[图片无法显示] {match.group(2)}")
        last = match.end()
    rest = (text or "")[last:]
    if rest.strip():
        st.markdown(rest)


def render_sources(sources: list[dict]) -> None:
    if not sources:
        return
    with st.expander(f"参考来源（{len(sources)} 个片段）", expanded=False):
        for index, source in enumerate(sources, 1):
            st.markdown(
                f"**{index}. {source.get('file_name') or '未知文件'} · "
                f"第 {source.get('page') or '?'} 页**"
            )
            score = source.get("score")
            if score is not None:
                st.caption(f"L2 距离 {score:.4f}（越小越相似）")
            if source.get("snippet"):
                st.caption(source["snippet"])
            for url in source.get("images", []):
                st.image(api.image_url(url), width=420)


st.title("💬 图文问答")
st.caption("回答只依据知识库检索结果；也可以上传一张图片，和问题一起交给视觉模型。")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "img_key" not in st.session_state:
    st.session_state.img_key = 0

with st.sidebar:
    st.divider()
    st.subheader("提问设置")
    top_k = st.slider("检索片段数", min_value=1, max_value=10, value=6)
    uploaded_image = st.file_uploader(
        "附加图片（可选）",
        type=["png", "jpg", "jpeg", "webp", "bmp"],
        key=f"img_{st.session_state.img_key}",
    )
    if uploaded_image is not None:
        st.image(uploaded_image, caption=uploaded_image.name, width=220)
    if st.button("清除图片", use_container_width=True):
        st.session_state.img_key += 1
        st.rerun()
    if st.button("清空对话", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message.get("image"):
            st.image(io.BytesIO(message["image"]), width=240)
        if message["role"] == "assistant":
            render_answer(message.get("content", ""))
            render_sources(message.get("sources", []))
        else:
            st.markdown(message.get("content", ""))

prompt = st.chat_input("例如：产品A 的销售额从哪个季度开始下降？")

if prompt:
    image_bytes = uploaded_image.getvalue() if uploaded_image is not None else None
    image_name = uploaded_image.name if uploaded_image is not None else "image.png"

    st.session_state.messages.append(
        {"role": "user", "content": prompt, "image": image_bytes}
    )
    with st.chat_message("user"):
        if image_bytes:
            st.image(io.BytesIO(image_bytes), width=240)
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("检索并生成回答..."):
            try:
                result = api.chat(prompt, image_bytes, image_name, top_k)
            except api.ApiError as exc:
                answer = f"调用失败：{exc}"
                st.error(answer)
                sources = []
            else:
                answer = result.get("answer", "")
                sources = result.get("sources", [])
                render_answer(answer)
                render_sources(sources)
                timings = result.get("timings", {})
                st.caption(
                    f"检索 {timings.get('retrieve_ms', 0)} ms · "
                    f"生成 {timings.get('llm_ms', 0)} ms · "
                    f"使用图片：{'是' if result.get('used_image') else '否'}"
                )

    st.session_state.messages.append(
        {"role": "assistant", "content": answer, "sources": sources}
    )
    st.session_state.img_key += 1
    st.rerun()
