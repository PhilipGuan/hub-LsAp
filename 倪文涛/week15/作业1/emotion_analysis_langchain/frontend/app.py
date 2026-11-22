import sys
from pathlib import Path

import httpx
import streamlit as st

# Streamlit executes this file as a script, so explicitly expose the project packages.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.model.schemas import result_adapter
from backend.common.settings import Settings
from frontend.sse import read_events


def main():
    st.set_page_config(page_title="情感分析聊天", page_icon="💬")
    st.title("情感分析聊天")
    st.caption("每条输入独立处理 · 正向鼓励 / 负面疏导 / 中性回复")
    settings = Settings()
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if st.button("清空页面记录"):
        st.session_state.messages = []
        st.rerun()
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    prompt = st.chat_input("说说你现在的心情，或问一个问题", max_chars=8000)
    if not prompt or not prompt.strip():
        return
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            with st.spinner("正在理解并回复…"):
                timeout = httpx.Timeout(settings.frontend_read_timeout_seconds, connect=10)
                with httpx.stream("POST", f"{settings.backend_url.rstrip('/')}/chat",
                                  json={"message": prompt}, headers={"Accept": "text/event-stream"},
                                  timeout=timeout) as response:
                    response.raise_for_status()
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        raise ValueError("后端没有返回 SSE 响应")
                    for event, payload in read_events(response.iter_lines()):
                        if event not in ("result", "error"):
                            continue
                        result = result_adapter.validate_python(payload)
                        if result.status == "error":
                            st.error(result.message)
                            return
                        st.markdown(result.reply)
                        st.caption(f"{result.emotion} · {result.agent}")
                        st.session_state.messages.append({"role": "assistant", "content": result.reply})
                        return
                    st.error("连接已结束，但未收到回复。请重新提交。")
        except httpx.HTTPStatusError as exc:
            st.error(f"后端请求失败（HTTP {exc.response.status_code}），请检查服务后重新提交。")
        except httpx.RequestError:
            st.error("无法连接后端或等待超时，请确认服务已启动。")
        except ValueError:
            st.error("后端返回的数据格式不正确，请检查后端日志。")


if __name__ == "__main__":
    main()
