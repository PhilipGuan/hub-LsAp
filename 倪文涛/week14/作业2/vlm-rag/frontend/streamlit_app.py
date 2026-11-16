import logging
from html import escape
from pathlib import Path

import httpx
import streamlit as st

from backend.config import load_config
from backend.models.document import DOCUMENT_EXTENSIONS, MAX_DOCUMENT_BYTES
from backend.models.qa import MAX_IMAGES, MAX_IMAGE_BYTES, MAX_QUESTION_CHARS


logger = logging.getLogger(__name__)


STATUSES = {
    "queued": "待解析",
    "processing": "解析中",
    "ready": "解析完成",
    "failed": "解析失败",
    "deleting": "清理中",
}

st.set_page_config(page_title="多模态知识库", layout="wide", initial_sidebar_state="expanded")
st.html(Path(__file__).with_name("dashboard.css"))
try:
    api_url = httpx.URL(load_config()["frontend"]["api_url"].rstrip("/"))
    if api_url.scheme not in {"http", "https"} or not api_url.host:
        raise ValueError("FastAPI 地址无效。")
    use_environment_proxy = api_url.host not in {"localhost", "127.0.0.1", "::1"}
except (KeyError, OSError, RuntimeError, ValueError, httpx.InvalidURL):
    st.error("请先在 config.yaml 设置 FastAPI 地址。")
    st.stop()


def request(method: str, path: str, *, timeout: float = 30, on_error=st.error, **kwargs):
    try:
        result = httpx.request(method, str(api_url) + path, timeout=timeout,
                               trust_env=use_environment_proxy, **kwargs)
        result.raise_for_status()
        return result
    except httpx.HTTPStatusError as exc:
        response = exc.response
        if response.is_redirect:
            location = response.headers.get("location")
            try:
                target = response.url.join(location) if location else None
            except httpx.InvalidURL:
                target = None
            logger.warning("api_redirect method=%s status=%s source_host=%r target_scheme=%r target_host=%r",
                           method, response.status_code, response.url.host,
                           target.scheme if target else None, target.host if target else None)
            on_error(f"后端返回重定向（{response.status_code}）。请检查 config.yaml 中的 FastAPI 地址。")
            return None
        try:
            detail = exc.response.json().get("detail")
        except ValueError:
            detail = None
        on_error(detail if isinstance(detail, str) else f"请求失败（{exc.response.status_code}）。")
    except httpx.RequestError:
        on_error("无法连接后端或请求超时，请稍后再试。")
    return None


def page_heading(title: str, detail: str):
    st.html(f'<div class="page-heading"><h1>{title}</h1><p>{detail}</p></div>')


def status_of(doc: dict) -> str:
    if doc["status"] == "deleting" and doc.get("error"):
        return "failed"
    return doc["status"]


def status_label(doc: dict) -> str:
    if doc["status"] == "deleting" and doc.get("error"):
        return "清理失败"
    return STATUSES.get(doc["status"], "未知状态")


def document_metrics(documents: list[dict] | None):
    values = ["—"] * 4 if documents is None else [
        len(documents),
        sum(status_of(doc) == "ready" for doc in documents),
        sum(status_of(doc) in {"queued", "processing"} for doc in documents),
        sum(status_of(doc) == "failed" for doc in documents),
    ]
    cards = "".join(
        f'<div class="stat-card"><div class="stat-label">{label}</div>'
        f'<div class="stat-value">{value}</div></div>'
        for label, value in zip(("全部文件", "可用于问答", "待解析 / 解析中", "失败"), values)
    )
    st.html(f'<div class="stat-grid">{cards}</div>')


def document_row(doc: dict):
    document_id = doc["document_id"]
    status = doc["status"]
    with st.container(key=f"file_row_{document_id}"):
        name, state, actions = st.columns([4, 2, 2], vertical_alignment="center")
        with name:
            date = doc.get("created_at", "")[:10]
            date_label = f'<span class="file-date">上传于 {escape(date)}</span>' if date else ""
            st.html(f'<div class="file-name">{escape(doc["filename"])}{date_label}</div>')
        with state:
            style = status_of(doc)
            if style not in STATUSES:
                style = "queued"
            st.html(f'<span class="status-pill status-{style}">{status_label(doc)}</span>')
        with actions:
            retry, delete = st.columns(2)
            if status == "failed" and retry.button("重试", key=f"retry-{document_id}"):
                if request("POST", f"/documents/{document_id}/retry") is not None:
                    st.rerun(scope="app")
            if status in {"ready", "failed", "deleting"}:
                with delete.popover("删除"):
                    st.write("删除原始文件、解析结果及索引。")
                    if st.button("确认删除", key=f"delete-{document_id}"):
                        if request("DELETE", f"/documents/{document_id}") is not None:
                            st.rerun(scope="app")
        if doc.get("error"):
            st.error(doc["error"])


@st.fragment(run_every="3s")
def document_dashboard():
    errors = []
    result = request("GET", "/documents", on_error=errors.append)
    documents = result.json() if result is not None else None
    document_metrics(documents)
    with st.container(key="file_list"):
        st.html('<h2 class="panel-heading">文件列表</h2>')
        if documents is None:
            st.error(f"无法加载文件列表。{errors[0]}")
            return
        if not documents:
            st.html('<div class="empty-state"><strong>还没有文档</strong>点击右上方「上传文件」开始。</div>')
            return

        search, filters = st.columns([3, 2])
        keyword = search.text_input("搜索文件名", placeholder="搜索文件名", label_visibility="collapsed")
        status = filters.selectbox("按状态筛选", ["全部状态", "解析完成", "待解析", "解析中", "解析失败", "清理中", "清理失败"],
                                   label_visibility="collapsed")
        visible = [
            doc for doc in documents
            if keyword.strip().casefold() in doc["filename"].casefold()
            and (status == "全部状态" or status_label(doc) == status)
        ]
        if not visible:
            st.html('<div class="empty-state"><strong>没有匹配的文件</strong>试试其他关键词或状态。</div>')
            return

        with st.container(key="table_header"):
            for column, label in zip(st.columns([4, 2, 2]), ("文件名", "解析状态", "操作")):
                column.html(escape(label))
        for doc in visible:
            document_row(doc)


@st.dialog("上传文件")
def upload_document():
    with st.form("upload"):
        document = st.file_uploader(
            "选择 PDF 或 Word 文件",
            type=[suffix.lstrip(".") for suffix in DOCUMENT_EXTENSIONS],
            max_upload_size=50,
            help="支持 .pdf、.doc、.docx，每次上传一个文件，最大 50 MiB。",
        )
        upload = st.form_submit_button("上传并解析", type="primary")
    if not upload:
        return
    if document is None:
        st.warning("请先选择文件。")
    elif document.size > MAX_DOCUMENT_BYTES:
        st.warning("文件大小不能超过 50 MiB。")
    else:
        file = (document.name, document, document.type or "application/octet-stream")
        if request("POST", "/documents", files={"file": file}) is not None:
            st.session_state["upload_notice"] = True
            st.rerun()


def show_documents():
    title, action = st.columns([5, 1], vertical_alignment="center")
    with title:
        page_heading("文件管理", "上传后自动解析，解析完成的文件可用于问答。")
    if action.button("上传文件", type="primary", icon=":material/upload_file:", use_container_width=True):
        upload_document()
    if st.session_state.pop("upload_notice", False):
        st.toast("文档已加入解析队列。")
    document_dashboard()


def message_text(text: str):
    st.html(f'<div class="chat-text">{escape(text)}</div>')


def show_question(turn: dict):
    with st.chat_message("user"):
        if turn["text"]:
            message_text(turn["text"])
        for image in turn["images"]:
            st.image(image, width=200)


def show_answer(turn: dict):
    if turn["errors"]:
        st.error(turn["errors"][0])
    for block in turn["blocks"]:
        if block["type"] == "text":
            message_text(block["text"])
        elif block["type"] == "image":
            result = request("GET", block["image_url"])
            if result is not None:
                st.image(result.content)


def start_new_chat():
    st.session_state["chat_history"] = []
    st.session_state["chat_generation"] += 1


def show_chat():
    history = st.session_state.setdefault("chat_history", [])
    generation = st.session_state.setdefault("chat_generation", 0)
    with st.container(key="chat_window", gap=None):
        with st.container(key="chat_header"):
            title, action = st.columns([5, 2], vertical_alignment="center")
            with title:
                page_heading("多模态 RAG 问答", "从已解析的文档中检索文字和图片。")
            action.button("新会话", on_click=start_new_chat, icon=":material/add:")

        chat_panel = st.container(key="chat_history", height="stretch", border=False, autoscroll=True)
        with chat_panel:
            empty_state = st.empty() if not history else None
            if empty_state is not None:
                empty_state.html('<div class="empty-state"><strong>开始提问</strong>输入文字或粘贴图片，查找文档中的相关内容。</div>')
            for turn in history:
                show_question(turn)
                with st.chat_message("assistant"):
                    show_answer(turn)

        composer = st.container(key="chat_composer")
        with composer:
            prompt = st.chat_input(
                "输入问题，或粘贴图片",
                key=f"chat_input_{generation}",
                max_chars=MAX_QUESTION_CHARS,
                max_upload_size=5,
                accept_file="multiple",
                file_type=["png", "jpg", "jpeg"],
            )
            st.caption("Enter 发送 · Shift+Enter 换行 · Ctrl+V / ⌘V 粘贴图片 · 最多 4 张，每张 5 MB")
    if prompt is None:
        return
    question, images = prompt.text.strip(), prompt.files
    if not question and not images:
        composer.warning("请输入问题或上传图片。")
        return
    if len(images) > MAX_IMAGES:
        composer.warning("最多上传 4 张图片。")
        return
    if any(image.size > MAX_IMAGE_BYTES for image in images):
        composer.warning("单张图片不能大于 5 MB。")
        return

    turn = {"text": question, "images": [image.getvalue() for image in images],
            "blocks": [], "errors": []}
    if empty_state is not None:
        empty_state.empty()
    with chat_panel:
        show_question(turn)
        with st.chat_message("assistant"):
            files = [("images", (image.name, image, image.type)) for image in images]
            with st.spinner("正在检索并生成答案…"):
                response = request("POST", "/ask", data={"text": question}, files=files, timeout=480,
                                   on_error=turn["errors"].append)
            if response is not None:
                turn["blocks"] = response.json()["blocks"]
            history.append(turn)
            show_answer(turn)


page = st.session_state.setdefault("active_page", "文件管理")
with st.sidebar:
    st.html('<div class="brand"><span class="brand-mark" aria-hidden="true">库</span>'
            '<span class="brand-name">多模态知识库</span></div>')
    for label, icon in (("文件管理", "folder_open"), ("多模态 RAG 问答", "forum")):
        st.button(label, icon=f":material/{icon}:", use_container_width=True,
                  type="primary" if page == label else "secondary",
                  on_click=st.session_state.update, args=({"active_page": label},))

if page == "文件管理":
    show_documents()
else:
    show_chat()
