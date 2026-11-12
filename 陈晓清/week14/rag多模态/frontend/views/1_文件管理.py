# -*- coding: utf-8 -*-
"""文件管理页：多文件上传、列表、删除、重新解析。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

import api_client as api

STATUS_ICON = {
    "pending": "🕓 待解析",
    "parsing": "⏳ 解析中",
    "done": "✅ 解析完成",
    "failed": "❌ 解析失败",
}

st.title("📄 文件管理")
st.caption("支持一次上传多个 PDF；后端通过 Kafka 交给 MinerU 异步解析。")

try:
    health = api.health()
    if not health.get("kafka", {}).get("ok"):
        st.warning("Kafka 当前不可用：上传后文件会保持“待解析”，请启动 Kafka 或让 worker 以轮询模式运行。")
except api.ApiError as exc:
    st.error(str(exc))
    st.stop()

uploaded = st.file_uploader(
    "选择 PDF（可多选）",
    type=["pdf"],
    accept_multiple_files=True,
    help="单文件不超过 50MB",
)

if st.button("上传并提交解析", type="primary", disabled=not uploaded):
    with st.spinner(f"正在上传 {len(uploaded)} 个文件..."):
        try:
            result = api.upload_files(uploaded)
        except api.ApiError as exc:
            st.error(f"上传失败：{exc}")
        else:
            st.session_state["last_upload"] = result
            st.rerun()

last = st.session_state.get("last_upload")
if last:
    for item in last.get("items", []):
        if item.get("kafka_queued"):
            st.success(f"已上传并进入 Kafka 队列：{item['filename']}（#{item['id']}）")
        else:
            st.warning(
                f"已上传但 Kafka 不可用：{item['filename']}（#{item['id']}），"
                "worker 轮询时会处理它。"
            )
    for item in last.get("rejected", []):
        st.error(f"已拒绝：{item['filename']} —— {item['reason']}")

st.divider()

try:
    data = api.list_files()
except api.ApiError as exc:
    st.error(f"读取文件列表失败：{exc}")
    st.stop()

items = data.get("items", [])
if not items:
    st.info("还没有文件，先上传一个 PDF 吧。")
    st.stop()

rows = [
    {
        "ID": item["id"],
        "文件名": item["filename"],
        "大小(MB)": round(item.get("size_bytes", 0) / 1024 / 1024, 2),
        "状态": STATUS_ICON.get(item["status"], item["status_text"]),
        "页数": item.get("page_count", 0),
        "Chunk": item.get("chunk_count", 0),
        "耗时(s)": item.get("parse_seconds", 0),
        "上传时间": (item.get("created_at") or "")[:19].replace("T", " "),
    }
    for item in items
]
st.dataframe(rows, use_container_width=True, hide_index=True)

st.subheader("文件操作")
options = {f"#{item['id']} · {item['filename']}": item["id"] for item in items}
selected_label = st.selectbox("选择文件", list(options.keys()))
selected_id = options[selected_label]

col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
if col1.button("重新解析", use_container_width=True):
    with st.spinner("已提交重新解析..."):
        try:
            api.reparse(selected_id)
        except api.ApiError as exc:
            st.error(str(exc))
        else:
            st.success("已提交，状态将变为待解析")
            st.rerun()

if col2.button("立即处理", use_container_width=True):
    with st.spinner("已提交处理..."):
        try:
            result = api.process_now(selected_id)
        except api.ApiError as exc:
            st.error(str(exc))
        else:
            mode = "Kafka" if result.get("kafka_queued") else "本地线程"
            st.success(f"已提交（{mode}）")
            st.rerun()

confirm = col3.checkbox("确认删除")
if col4.button("删除文件", type="secondary", disabled=not confirm, use_container_width=True):
    with st.spinner("正在删除记录、文件和向量..."):
        try:
            api.delete_file(selected_id)
        except api.ApiError as exc:
            st.error(str(exc))
        else:
            st.success("已删除")
            st.rerun()
