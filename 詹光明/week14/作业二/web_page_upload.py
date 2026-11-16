"""
知识库管理页（生产者）。

- 支持一次上传多个 PDF
- 文件落盘 + 元信息入 sqlite
- 向 Kafka 投递解析任务
- 实时展示每个文件的解析状态（待解析 / 解析中 / 解析完成 / 解析失败）
"""
import hashlib
import uuid
from pathlib import Path

import streamlit as st

from config import UPLOAD_DIR
from kafka_utils import publish_parse_task
from orm_model import (
    STATE_DONE,
    STATE_FAILED,
    STATE_PENDING,
    STATE_PROCESSING,
    create_file,
    find_by_sha,
    list_files,
    pop_file,
    update_file,
)
from ui_common import render_sidebar, safe_remove_dir, status_badge
from vector_store import delete_document


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _save_upload(uploaded_file) -> Path:
    data = uploaded_file.getvalue()
    suffix = Path(uploaded_file.name).suffix.lower() or ".pdf"
    destination = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    destination.write_bytes(data)
    return destination


def submit_upload(uploaded_file) -> str:
    """保存一个上传文件并投递解析任务，返回给用户看的结果文案。"""
    name = uploaded_file.name
    data = uploaded_file.getvalue()
    digest = _sha256(data)

    existing = find_by_sha(digest)
    if existing is not None:
        # 已经解析过 / 正在解析：直接跳过，避免重复入库
        if existing.filestate in (STATE_PENDING, STATE_PROCESSING, STATE_DONE):
            return f"跳过「{name}」：相同文件已存在（{existing.filestate}）"

        # 上次失败：复用原记录重新投递
        if Path(existing.filepath).is_file():
            update_file(existing.id, filestate=STATE_PENDING, error=None)
            try:
                publish_parse_task(existing.id, existing.filepath, existing.filename)
                return f"已重新提交「{name}」"
            except Exception as exc:
                update_file(existing.id, filestate=STATE_FAILED, error=f"Kafka 提交失败: {exc}")
                return f"「{name}」提交 Kafka 失败：{exc}"

    save_path = _save_upload(uploaded_file)
    file_id = create_file(name, str(save_path), digest, len(data))

    try:
        publish_parse_task(file_id, save_path, name)
        return f"已提交「{name}」（file_id={file_id}）"
    except Exception as exc:
        update_file(file_id, filestate=STATE_FAILED, error=f"Kafka 提交失败: {exc}")
        return f"「{name}」已保存，但提交 Kafka 失败：{exc}"


def _delete_record(record) -> None:
    # 先删向量，再删元信息和磁盘文件
    delete_document(record.id)
    info = pop_file(record.id)

    if info:
        try:
            Path(info["filepath"]).unlink(missing_ok=True)
        except Exception:
            pass
        if info.get("md_path"):
            safe_remove_dir(Path(info["md_path"]).parent)
    st.toast(f"已删除「{record.filename}」", icon="🗑️")


def _reparse(record) -> None:
    if not Path(record.filepath).is_file():
        st.toast("源文件已丢失，无法重新解析", icon="⚠️")
        return
    update_file(record.id, filestate=STATE_PENDING, error=None)
    try:
        publish_parse_task(record.id, record.filepath, record.filename)
        st.toast(f"已重新提交「{record.filename}」", icon="📤")
    except Exception as exc:
        update_file(record.id, filestate=STATE_FAILED, error=f"Kafka 提交失败: {exc}")
        st.toast(f"提交失败：{exc}", icon="❌")
    st.rerun()


@st.fragment(run_every="3s")
def render_file_list() -> None:
    """文件列表 + 状态，每 3 秒自动刷新。"""
    files = list_files()
    if not files:
        st.info("暂无文件，请先上传 PDF。")
        return

    st.caption(f"共 {len(files)} 个文件 · 状态每 3 秒自动刷新")
    for record in files:
        with st.container(border=True):
            col_info, col_state, col_reparse, col_delete = st.columns([5, 2, 1.3, 1])

            with col_info:
                st.markdown(f"**{record.filename}**")
                size_kb = (record.filesize or 0) / 1024
                st.caption(
                    f"ID {record.id} · {size_kb:.1f} KB · "
                    f"上传于 {record.created_at:%Y-%m-%d %H:%M:%S}"
                )

            with col_state:
                st.markdown(status_badge(record.filestate))

            if record.filestate in (STATE_PENDING, STATE_FAILED):
                if col_reparse.button(
                    "重新解析", key=f"reparse_{record.id}", width="stretch"
                ):
                    _reparse(record)

            if col_delete.button(
                "删除", key=f"delete_{record.id}", width="stretch"
            ):
                _delete_record(record)
                st.rerun()

            if record.filestate == STATE_FAILED and record.error:
                with st.expander("查看失败原因"):
                    st.code(record.error)


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
render_sidebar()

st.title("📚 知识库文件")
st.markdown(
    "上传 PDF 后，任务会通过 **Kafka** 投递给后台 worker，"
    "由 **MinerU** 解析为 markdown + 图片，再切块并写入 **Milvus**。"
)

render_file_list()

st.divider()
st.subheader("上传 PDF")
uploaded_files = st.file_uploader(
    "选择 PDF 文件（可多选）",
    type=["pdf"],
    accept_multiple_files=True,
    help="支持一次选择多个 PDF，系统会自动去重并排队解析。",
)

if st.button("上传并提交解析", type="primary", disabled=not uploaded_files):
    results = [submit_upload(item) for item in uploaded_files]
    st.session_state["upload_results"] = results
    st.rerun()

for message in st.session_state.pop("upload_results", []):
    if "失败" in message or "跳过" in message:
        st.warning(message)
    else:
        st.success(message)
