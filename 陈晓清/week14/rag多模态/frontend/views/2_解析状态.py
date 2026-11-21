# -*- coding: utf-8 -*-
"""解析状态页：待解析 / 解析中 / 解析完成 / 解析失败，自动刷新。"""

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


def render_body() -> None:
    try:
        summary = api.summary()
        data = api.list_files()
    except api.ApiError as exc:
        st.error(f"读取状态失败：{exc}")
        return

    cols = st.columns(5)
    cols[0].metric("待解析", summary.get("pending", 0))
    cols[1].metric("解析中", summary.get("parsing", 0))
    cols[2].metric("解析完成", summary.get("done", 0))
    cols[3].metric("解析失败", summary.get("failed", 0))
    cols[4].metric("Chunk 总数", summary.get("chunks", 0))

    items = data.get("items", [])
    if not items:
        st.info("暂无文件。")
        return

    for item in items:
        icon = STATUS_ICON.get(item["status"], item["status_text"])
        title = f"{icon} · #{item['id']} {item['filename']}"
        with st.container(border=True):
            head = st.columns([3, 1.2, 1.2, 1.5])
            head[0].markdown(f"**{title}**")
            head[1].caption(f"页数：{item.get('page_count', 0)}")
            head[2].caption(f"Chunk：{item.get('chunk_count', 0)}")
            head[3].caption(f"耗时：{item.get('parse_seconds', 0)} s")

            if item.get("status") == "parsing":
                st.progress(0.5, text="MinerU 正在解析，请稍候……")
            if item.get("error"):
                st.error(item["error"])
                if st.button("重新解析", key=f"retry_{item['id']}"):
                    try:
                        api.reparse(item["id"])
                    except api.ApiError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
            if not item.get("error"):
                st.caption(
                    f"上传：{(item.get('created_at') or '')[:19].replace('T', ' ')}　"
                    f"更新：{(item.get('updated_at') or '')[:19].replace('T', ' ')}"
                )


st.title("⏳ 解析状态")
st.caption("状态由后端 worker 实时写入 SQLite；Kafka 不可用时 worker 会自动改为轮询。")

auto = st.toggle("自动刷新（3 秒）", value=True)

if auto:

    @st.fragment(run_every=3)
    def panel() -> None:
        render_body()

    panel()
else:
    if st.button("手动刷新", type="primary"):
        st.rerun()
    render_body()
