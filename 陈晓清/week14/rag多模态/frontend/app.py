# -*- coding: utf-8 -*-
"""多模态 RAG 前端入口：只通过 HTTP 调后端。"""

from __future__ import annotations

import streamlit as st

from api_client import ApiError, health

st.set_page_config(
    page_title="多模态 RAG 问答",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data(ttl=15, show_spinner=False)
def load_health() -> dict | None:
    try:
        return health()
    except ApiError:
        return None


def render_health() -> None:
    data = load_health()
    st.markdown("### 系统状态")
    if data is None:
        st.error("后端不可达")
        st.code("uvicorn backend.app.main:app --port 8000 --reload")
        return

    labels = {
        "api": "后端",
        "kafka": "Kafka",
        "milvus": "Milvus",
        "llm": "大模型",
        "mineru": "MinerU",
    }
    for key, label in labels.items():
        item = data.get(key) or {}
        ok = item.get("ok")
        detail = item.get("detail", "")
        if ok:
            st.success(f"{label}：正常", icon="✅")
        else:
            st.warning(f"{label}：{detail}", icon="⚠️")
        if detail and ok:
            st.caption(detail)

    if st.button("刷新状态", use_container_width=True):
        load_health.clear()
        st.rerun()


pages = [
    st.Page("views/1_文件管理.py", title="文件管理", icon="📄", default=True),
    st.Page("views/2_解析状态.py", title="解析状态", icon="⏳"),
    st.Page("views/3_图文问答.py", title="图文问答", icon="💬"),
]

with st.sidebar:
    st.markdown("## 多模态 RAG")
    st.caption("MinerU 解析 · bge / CLIP 检索 · qwen3.8-max 问答")
    render_health()

st.navigation(pages).run()
