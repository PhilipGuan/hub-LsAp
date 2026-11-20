"""Streamlit 页面共用的小组件。"""
import shutil
from pathlib import Path
from typing import List, Optional, Union

import streamlit as st

from config import (
    KAFKA_BOOTSTRAP_SERVERS,
    KAFKA_TOPIC,
    LLM_MODEL,
    MILVUS_URI,
    PARSER_BACKEND,
    PROCESSED_DIR,
)
from orm_model import STATE_DONE, STATE_FAILED, STATE_PENDING, STATE_PROCESSING
from text_utils import iter_markdown_segments, resolve_image_path

STATUS_BADGE = {
    STATE_PENDING: "🕒 待解析",
    STATE_PROCESSING: "⏳ 解析中",
    STATE_DONE: "✅ 解析完成",
    STATE_FAILED: "❌ 解析失败",
}


def status_badge(state: str) -> str:
    return STATUS_BADGE.get(state, state or "-")


def render_sidebar() -> None:
    """在侧边栏展示当前生效的配置，方便排查环境问题。"""
    with st.sidebar:
        st.markdown("### ⚙️ 当前配置")
        st.caption(f"Kafka：`{KAFKA_BOOTSTRAP_SERVERS}` / `{KAFKA_TOPIC}`")
        st.caption(f"Milvus：`{MILVUS_URI}`")
        st.caption(f"解析器：`{PARSER_BACKEND}`")
        st.caption(f"问答模型：`{LLM_MODEL}`")


def render_markdown_with_images(
    markdown: str,
    search_dirs: Optional[List[Union[str, Path]]] = None,
) -> None:
    """
    渲染“图文混排”的回答：
    - 普通文本交给 st.markdown
    - 图片交给 st.image（支持本地文件和 http 链接）
    """
    for kind, value, alt in iter_markdown_segments(markdown or ""):
        if kind == "text":
            if value.strip():
                st.markdown(value)
            continue

        target = resolve_image_path(value, search_dirs or [str(PROCESSED_DIR)])
        if target is None:
            st.caption(f"（图片未找到：{value}）")
            continue

        try:
            st.image(target, caption=alt or None, width="stretch")
        except Exception:
            # 某些 Streamlit 版本不支持 width="stretch"
            st.image(target, caption=alt or None)


def safe_remove_dir(path: Union[str, Path, None]) -> None:
    """只允许删除 PROCESSED_DIR 下的目录，防止误删。"""
    if not path:
        return
    try:
        target = Path(path).resolve()
        root = PROCESSED_DIR.resolve()
        target.relative_to(root)
    except Exception:
        return
    if target != root and target.exists():
        shutil.rmtree(target, ignore_errors=True)
