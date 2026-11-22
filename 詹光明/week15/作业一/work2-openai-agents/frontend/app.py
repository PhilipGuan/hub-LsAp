"""前端进程：Streamlit 聊天界面。

启动方式（在 work2-openai-agents 目录下）：

    streamlit run frontend/app.py

前端只通过 HTTP 调后端，不直接 import Agent 代码，
因此前后端可以分别启动、分别重启。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 复用项目根目录的 .env（可选）。
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

DEFAULT_BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
REQUEST_TIMEOUT = float(os.getenv("FRONTEND_TIMEOUT", "90"))
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "4000"))

LABEL_ZH = {"positive": "正向", "negative": "负面", "neutral": "中性"}
EMOTION_ZH = {
    "joy": "喜悦",
    "gratitude": "感激",
    "pride": "自豪",
    "sadness": "难过",
    "anxiety": "焦虑",
    "anger": "愤怒",
    "tiredness": "疲惫",
    "loneliness": "孤独",
    "disappointment": "失望",
    "distress": "痛苦",
    "neutral": "平静",
    "unknown": "未知",
}
AVATARS = {"user": "🧑", "assistant": "🌿"}


# ---------------------------------------------------------------------------
# HTTP 调用
# ---------------------------------------------------------------------------
@st.cache_data(ttl=10, show_spinner=False)
def check_health(backend_url: str) -> tuple[bool, str]:
    """带缓存的健康检查，避免每次重跑都打后端。"""

    if not backend_url.strip():
        return False, "未配置后端地址"
    try:
        response = httpx.get(
            f"{backend_url.rstrip('/')}/health", timeout=3.0
        )
        response.raise_for_status()
        data = response.json()
        model = data.get("model", "?")
        if not data.get("api_key_configured", False):
            return True, f"已连接 · 模型 {model} · 未配置 API key"
        return True, f"已连接 · 模型 {model}"
    except Exception:  # noqa: BLE001 - 连接失败只影响提示
        return False, "未连接"


def post_chat(
    backend_url: str, message: str, thread_id: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    payload: dict[str, Any] = {"message": message}
    if thread_id:
        payload["thread_id"] = thread_id

    try:
        response = httpx.post(
            f"{backend_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json(), None
    except httpx.ConnectError:
        return None, (
            f"无法连接后端 {backend_url}。请先在 work2-openai-agents 目录运行：\n"
            "`uvicorn backend.main:app --host 127.0.0.1 --port 8000`"
        )
    except httpx.TimeoutException:
        return None, f"后端响应超时（>{REQUEST_TIMEOUT:.0f}s），请稍后重试。"
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:  # noqa: BLE001
            detail = exc.response.text[:300]
        return None, f"后端返回 {exc.response.status_code}：{detail}"
    except Exception as exc:  # noqa: BLE001 - 前端需要稳定展示
        return None, f"请求失败：{exc}"


def delete_thread(backend_url: str, thread_id: str) -> None:
    try:
        httpx.delete(
            f"{backend_url.rstrip('/')}/api/thread/{thread_id}", timeout=10.0
        )
    except Exception:  # noqa: BLE001 - 删除失败不阻塞新会话
        pass


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def init_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("thread_id", None)
    st.session_state.setdefault("backend_url", DEFAULT_BACKEND_URL)


def render_emotion(emotion: dict[str, Any]) -> None:
    label = str(emotion.get("label", "neutral"))
    emotion_key = str(emotion.get("emotion", "unknown"))
    confidence = float(emotion.get("confidence", 0.0) or 0.0)
    intensity = int(emotion.get("intensity", 3) or 3)

    title = (
        f"情绪识别：{LABEL_ZH.get(label, label)} · "
        f"{EMOTION_ZH.get(emotion_key, emotion_key)} · "
        f"置信度 {confidence:.0%}"
    )
    with st.expander(title, expanded=False):
        col1, col2, col3 = st.columns(3)
        col1.metric("情感倾向", LABEL_ZH.get(label, label))
        col2.metric("细分情绪", EMOTION_ZH.get(emotion_key, emotion_key))
        col3.metric("强度", f"{intensity}/5")
        st.progress(min(max(confidence, 0.0), 1.0))
        st.markdown(f"**判断依据：** {emotion.get('reason', '—')}")
        keywords = emotion.get("keywords") or []
        if keywords:
            st.markdown(
                "**关键词：** " + "、".join(f"`{item}`" for item in keywords)
            )
        if emotion.get("risk"):
            st.warning(
                "检测到危机相关表达，已切换到关怀模式。"
                "如果你正处于危险中，请立即联系身边的人，"
                "或拨打 120 / 110、全国心理援助热线 12356。"
            )


def render_messages() -> None:
    if not st.session_state.messages:
        with st.chat_message("assistant", avatar=AVATARS["assistant"]):
            st.markdown(
                "你好，我在这里。你可以说说今天发生了什么，"
                "也可以只说一句“有点累”。我会先听你说。"
            )
        return

    for item in st.session_state.messages:
        role = item.get("role", "assistant")
        with st.chat_message(role, avatar=AVATARS.get(role)):
            st.markdown(item.get("content", ""))
            if role != "assistant":
                continue

            suggestions = item.get("suggestions") or []
            if suggestions:
                st.markdown("**可以试试：**")
                for suggestion in suggestions:
                    st.markdown(f"- {suggestion}")

            if item.get("emotion"):
                render_emotion(item["emotion"])

            if item.get("trace"):
                st.caption("流程：" + " → ".join(item["trace"]))

            if item.get("error"):
                st.error(item["error"])


def render_sidebar() -> None:
    st.sidebar.title("🌿 情绪陪伴助手")
    st.sidebar.caption("进程1 识别情绪 · 进程2 鼓励 / 排解 · OpenAI Agents SDK 串联")

    st.sidebar.text_input(
        "后端地址",
        key="backend_url",
        help="后端默认运行在 http://127.0.0.1:8000",
    )

    healthy, status = check_health(st.session_state.backend_url)
    if healthy:
        st.sidebar.success(status)
    else:
        st.sidebar.warning(f"{status} —— 请先启动后端服务")

    if st.sidebar.button("🔄 重新检查连接", width="stretch"):
        check_health.clear()
        st.rerun()

    if st.sidebar.button("🆕 新对话", width="stretch"):
        if st.session_state.thread_id:
            delete_thread(
                st.session_state.backend_url, st.session_state.thread_id
            )
        st.session_state.messages = []
        st.session_state.thread_id = None
        st.rerun()

    st.sidebar.divider()
    st.sidebar.caption(
        f"会话 ID：`{st.session_state.thread_id or '（新会话）'}`"
    )

    last_emotion = next(
        (
            item["emotion"]
            for item in reversed(st.session_state.messages)
            if item.get("emotion")
        ),
        None,
    )
    if last_emotion:
        label = LABEL_ZH.get(
            last_emotion.get("label", ""), last_emotion.get("label", "")
        )
        emotion_key = str(last_emotion.get("emotion", ""))
        st.sidebar.metric("最近一次情绪", label)
        st.sidebar.caption(
            f"{EMOTION_ZH.get(emotion_key, emotion_key)} · "
            f"强度 {last_emotion.get('intensity', '?')}/5"
        )


def handle_input() -> None:
    prompt = st.chat_input(
        "说说你现在的心情……（Enter 发送）",
        max_chars=MAX_MESSAGE_CHARS,
    )
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.spinner("正在倾听，也在认真感受你的情绪…"):
        data, error = post_chat(
            st.session_state.backend_url, prompt, st.session_state.thread_id
        )

    if error or data is None:
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": "抱歉，这次没能收到后端的回应。",
                "error": error or "未知错误",
            }
        )
        st.rerun()

    st.session_state.thread_id = data.get("thread_id")
    reply = data.get("reply") or {}
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": reply.get("reply_text", "（后端没有返回回复正文）"),
            "suggestions": reply.get("suggestions") or [],
            "strategy": reply.get("strategy"),
            "emotion": data.get("emotion"),
            "trace": data.get("trace") or [],
        }
    )
    st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="情绪陪伴助手",
        page_icon="🌿",
        layout="centered",
    )
    init_state()
    render_sidebar()

    st.title("🌿 情绪陪伴助手")
    st.caption("先被看见，再被接住。这里不做评判，只陪你把情绪说清楚。")

    render_messages()
    handle_input()


if __name__ == "__main__":
    main()
