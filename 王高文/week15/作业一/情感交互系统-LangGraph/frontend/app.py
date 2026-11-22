"""显示多轮情感对话和可选的 Agent 调试信息。"""

import os
from collections.abc import Callable
from typing import Any, TypeVar

import httpx2
import streamlit as st
from pydantic import TypeAdapter, ValidationError

from backend.models.schemas import ChatDebugInfo, ChatRequest, ChatResponse, HistoryMessage


_API_BASE_URL = (os.getenv("CHAT_API_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
_HISTORY = TypeAdapter(list[HistoryMessage])
_Response = TypeVar("_Response")


def _call_backend(
    method: str, path: str, parse: Callable[[bytes], _Response], **kwargs: Any
) -> _Response | None:
    try:
        response = httpx2.request(method, f"{_API_BASE_URL}{path}", timeout=90.0, **kwargs)
        response.raise_for_status()
        return parse(response.content)
    except httpx2.RequestError:
        st.error("无法连接聊天服务，请确认 FastAPI 已启动后重试。")
    except httpx2.HTTPStatusError:
        st.error("聊天服务暂时无法完成请求，请稍后再试。")
    except ValidationError:
        st.error("聊天服务返回的数据格式有误，请稍后再试。")
    return None


def _set_conversation(
    session_id: str | None, messages: list[HistoryMessage], debug: ChatDebugInfo | None = None
) -> None:
    st.session_state.session_id = session_id
    st.session_state.messages = messages
    st.session_state.last_debug = debug
    if session_id is not None:
        st.query_params["session_id"] = session_id
    elif "session_id" in st.query_params:
        del st.query_params["session_id"]


def _restore_conversation(session_id: str) -> None:
    if not session_id.strip():
        st.warning("请输入要恢复的会话 ID。")
        return

    history = _call_backend(
        "GET", "/history", _HISTORY.validate_json, params={"session_id": session_id}
    )
    if history is None:
        return
    if not history:
        st.warning("该会话暂无已保存的消息。")
        return
    _set_conversation(session_id, history)


st.set_page_config(page_title="情感对话", page_icon=":material/chat_bubble:")
st.title("情感对话")

if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.session_id = None
    st.session_state.last_debug = None
    if saved_id := st.query_params.get("session_id"):
        _restore_conversation(saved_id)

with st.sidebar:
    if st.button("新对话", key="new_chat", icon=":material/add:", width="stretch"):
        _set_conversation(None, [])

    with st.form("restore_chat", border=False):
        restore_id = st.text_input("会话 ID", placeholder="粘贴已有会话 ID")
        restore = st.form_submit_button("恢复会话", width="stretch")
    if restore:
        _restore_conversation(restore_id)

    debug_enabled = st.toggle("调试模式", key="debug_enabled")

for item in st.session_state.messages:
    with st.chat_message(item["role"]):
        st.markdown(item["content"])

prompt = st.chat_input("说说你想聊的事", submit_mode="disable")
if prompt and prompt.strip():
    st.session_state.last_debug = None
    with st.chat_message("user"):
        st.markdown(prompt)

    payload = ChatRequest(message=prompt, session_id=st.session_state.session_id, debug=debug_enabled)
    with st.chat_message("assistant"):
        with st.spinner("正在生成回复…"):
            result = _call_backend(
                "POST",
                "/chat",
                ChatResponse.model_validate_json,
                json=payload.model_dump(exclude_none=True),
            )
        if result is not None:
            st.markdown(result.reply)
            _set_conversation(
                result.session_id,
                [
                    *st.session_state.messages,
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": result.reply},
                ],
                result.debug,
            )

if st.session_state.session_id is not None:
    st.sidebar.caption("当前会话 ID")
    st.sidebar.code(st.session_state.session_id, language=None)

if debug_enabled and st.session_state.last_debug is not None:
    st.sidebar.subheader("本轮调试信息")
    st.sidebar.json(st.session_state.last_debug.model_dump(exclude_none=True))
