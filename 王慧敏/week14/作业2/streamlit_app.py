# -*- coding: utf-8 -*-
"""Streamlit 前端：PDF 多文件上传、解析状态展示、多模态 RAG 问答。

运行方式（需要先在另外两个终端启动 worker 和后端 API）:
    python -m app.worker
    uvicorn app.main:app --reload --port 8000
    streamlit run streamlit_app.py

本文件只通过 HTTP 调用 app/main.py 暴露的 REST 接口，不直接依赖 Kafka/MinerU/
Chroma 等后端实现，方便前后端独立运行、独立重启。
"""

import base64
import os
import time

import requests
import streamlit as st

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")

STATUS_BADGE = {
    "待解析": "⏳ 待解析",
    "解析中": "🔄 解析中",
    "解析完成": "✅ 解析完成",
    "解析失败": "❌ 解析失败",
}

st.set_page_config(page_title="Kafka 多模态 RAG 问答", page_icon="📄", layout="wide")

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []  # [{"role": "user"/"assistant", "text": str, "images": [bytes,...]}]
if "pending_image" not in st.session_state:
    st.session_state.pending_image = None


def api_get(path):
    resp = requests.get(f"{API_BASE}{path}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def api_post(path, **kwargs):
    resp = requests.post(f"{API_BASE}{path}", timeout=120, **kwargs)
    resp.raise_for_status()
    return resp.json()


def data_url_to_bytes(data_url: str) -> bytes:
    """data:image/xxx;base64,.... -> 原始字节，交给 st.image 显示。"""
    _, b64data = data_url.split(",", 1)
    return base64.b64decode(b64data)


def file_to_data_url(uploaded_file) -> str:
    raw = uploaded_file.getvalue()
    mime = uploaded_file.type or "image/png"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


st.title("📄 Kafka 多模态 RAG 问答")
st.caption("前端上传 PDF → Kafka 异步解耦 → MinerU 解析 → 向量入库 → 图文混排问答")

# ---------------------------------------------------------------------------
# 区域1：上传 PDF
# ---------------------------------------------------------------------------
with st.container(border=True):
    st.subheader("1. 上传 PDF")
    uploaded_pdfs = st.file_uploader(
        "可多选 PDF 文件", type=["pdf"], accept_multiple_files=True, key="pdf_uploader"
    )
    if st.button("开始上传", disabled=not uploaded_pdfs):
        files = [
            ("files", (f.name, f.getvalue(), "application/pdf")) for f in uploaded_pdfs
        ]
        try:
            result = api_post("/api/documents/upload", files=files)
            st.success(f"已提交 {len(result.get('documents', []))} 个文件，等待解析")
        except requests.RequestException as e:
            st.error(f"上传失败：{e}")

# ---------------------------------------------------------------------------
# 区域2：解析状态
# ---------------------------------------------------------------------------
with st.container(border=True):
    st.subheader("2. 解析状态")
    auto_refresh = st.checkbox("自动刷新（每 2 秒）", value=True)

    try:
        docs = api_get("/api/documents").get("documents", [])
    except requests.RequestException as e:
        docs = []
        st.error(f"获取文档列表失败：{e}")

    if docs:
        table_rows = [
            {
                "文件名": d["filename"],
                "状态": STATUS_BADGE.get(d["status"], d["status"]),
                "文档ID": d["doc_id"],
                "更新时间": d.get("updated_at", ""),
            }
            for d in docs
        ]
        st.dataframe(table_rows, use_container_width=True, hide_index=True)
    else:
        st.info("暂无文档，请先上传 PDF。")

# ---------------------------------------------------------------------------
# 区域3：多模态问答
# ---------------------------------------------------------------------------
with st.container(border=True):
    st.subheader("3. 多模态问答")

    done_docs = [d for d in docs if d["status"] == "解析完成"]
    doc_options = {f"{d['filename']} ({d['doc_id'][:8]})": d["doc_id"] for d in done_docs}
    selected_labels = st.multiselect(
        "限定检索的文档（不选 = 检索全部已解析完成的文档）",
        options=list(doc_options.keys()),
    )
    selected_doc_ids = [doc_options[label] for label in selected_labels] or None

    attach_image = st.file_uploader(
        "📎 附带图片一起提问（可选）", type=["png", "jpg", "jpeg"], key="qa_image_uploader"
    )

    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["text"])
            for img_bytes in msg.get("images", []):
                st.image(img_bytes, width=280)

    question = st.chat_input("输入问题，回车发送")
    if question:
        user_image_data_urls = [file_to_data_url(attach_image)] if attach_image else None

        st.session_state.chat_history.append(
            {
                "role": "user",
                "text": question,
                "images": [data_url_to_bytes(u) for u in (user_image_data_urls or [])],
            }
        )

        with st.spinner("检索资料并生成回答..."):
            try:
                result = api_post(
                    "/api/chat",
                    json={
                        "question": question,
                        "doc_ids": selected_doc_ids,
                        "image_data_urls": user_image_data_urls,
                    },
                )
                answer = result.get("answer", "")
                context_images = [
                    data_url_to_bytes(u) for u in result.get("context_images", [])
                ]
                st.session_state.chat_history.append(
                    {"role": "assistant", "text": answer, "images": context_images}
                )
            except requests.RequestException as e:
                st.session_state.chat_history.append(
                    {"role": "assistant", "text": f"调用失败：{e}", "images": []}
                )

        st.rerun()

if auto_refresh:
    time.sleep(2)
    st.rerun()
