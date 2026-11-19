"""作业1 · Streamlit 前端：流式输出 + 会话管理 + 双后端切换 + 记忆展示。

运行（在「作业1-情感助手」目录下）：
    streamlit run frontend.py
需先启动两个后端：python -m api.langgraph_server / python -m api.agents_server
"""
from __future__ import annotations

import json

import requests
import streamlit as st

from core.config import AGENTS_PORT, LANGGRAPH_PORT

BACKENDS = {
    "LangGraph": f"http://127.0.0.1:{LANGGRAPH_PORT}",
    "OpenAI-Agents": f"http://127.0.0.1:{AGENTS_PORT}",
}

SENTIMENT_EMOJI = {"positive": "😊", "negative": "😔", "neutral": "😐"}
SENTIMENT_CN = {"positive": "正向", "negative": "负面", "neutral": "中性"}
INTENSITY_CN = {"high": "强", "medium": "中", "low": "弱"}

st.set_page_config(page_title="情感助手", page_icon="💬", layout="centered")
st.title("情感助手 · 应用级双框架对比")

if "sessions" not in st.session_state:
    # backend 名 → 当前 session_id（None 表示尚未选择会话）
    st.session_state.sessions = {name: None for name in BACKENDS}
if "chat_log" not in st.session_state:
    # (backend, session_id) → 消息列表
    st.session_state.chat_log: dict = {}


def api(backend: str, method: str, path: str, **kwargs):
    """请求当前后端，返回 (ok, data)。"""
    url = BACKENDS[backend] + path
    try:
        resp = getattr(requests, method)(url, timeout=90, **kwargs)
        if resp.status_code == 404:
            return False, None
        resp.raise_for_status()
        return True, resp.json()
    except requests.exceptions.ConnectionError:
        st.error(f"无法连接 {backend} 后端（{BACKENDS[backend]}），请先启动服务。")
        return False, None
    except Exception as e:
        st.error(f"请求失败：{e}")
        return False, None


def render_sentiment_badge(sentiment: str, intensity: str) -> str:
    return (
        f"{SENTIMENT_EMOJI.get(sentiment, '😐')} "
        f"情感：{SENTIMENT_CN.get(sentiment, sentiment)} · "
        f"强度：{INTENSITY_CN.get(intensity, intensity)}"
    )


# ---------------------------------------------------------------- 侧边栏

backend_name = st.sidebar.radio("选择后端框架", list(BACKENDS.keys()))
base_url = BACKENDS[backend_name]
st.sidebar.caption(f"后端地址：{base_url}")

st.sidebar.markdown("---")
st.sidebar.write("两套后端实现相同逻辑：")
st.sidebar.write("记忆检索 → 情感识别 → 分支回应 → 记忆回写")

# 会话管理
st.sidebar.markdown("---")
if st.sidebar.button("新建会话", use_container_width=True):
    # 立即在后端创建会话（而非等首条消息），历史列表马上可见
    ok, new_session = api(backend_name, "post", "/sessions")
    if ok and new_session:
        st.session_state.sessions[backend_name] = new_session["session_id"]
    st.rerun()

ok, sessions = api(backend_name, "get", "/sessions")
if ok and sessions:
    options = {
        f"{s['title']}（{s['session_id'][:8]}）": s["session_id"] for s in sessions
    }
    options["➕ 新会话…"] = None
    current = st.session_state.sessions[backend_name]
    if current in options.values():
        idx = list(options.values()).index(current)
    elif current is None:
        idx = len(options) - 1  # 无当前会话 → 停在"新会话"占位
    else:
        idx = 0
    label = st.sidebar.selectbox("历史会话", list(options.keys()), index=idx)
    chosen = options[label]
    if chosen is not None:
        # 只在选中真实会话时更新；选"新会话"占位不覆盖
        st.session_state.sessions[backend_name] = chosen

session_id = st.session_state.sessions[backend_name]

if session_id:
    ok, detail = api(backend_name, "get", f"/sessions/{session_id}")
    if not ok:
        # 会话可能已被删除：重置选择
        st.session_state.sessions[backend_name] = None
        st.rerun()
    if st.sidebar.button("删除当前会话", use_container_width=True):
        api(backend_name, "delete", f"/sessions/{session_id}")
        st.session_state.sessions[backend_name] = None
        st.rerun()

# ---------------------------------------------------------------- 主区域

if session_id is None:
    st.info("点击左侧「**新建会话**」按钮或选择一个历史会话开始对话。")
    st.stop()

st.caption(f"当前会话：`{session_id[:12]}…` · 框架：**{backend_name}**")

# 长期记忆画像
if detail and detail["memories"]:
    with st.expander("长期记忆画像", expanded=False):
        for m in detail["memories"]:
            if m["key"] == "emotion_trend":
                v = m["value"]
                st.write(
                    f"情绪趋势：😊 {v.get('positive', 0)} · "
                    f"😔 {v.get('negative', 0)} · 😐 {v.get('neutral', 0)}"
                )
            elif m["key"] == "key_events" and m["value"].get("items"):
                st.write("关键事件：" + "；".join(m["value"]["items"]))
            elif m["key"] == "topics" and m["value"].get("items"):
                st.write("常聊主题：" + "；".join(m["value"]["items"]))
            elif m["key"] == "last_summary" and m["value"].get("text"):
                st.write("近况：" + m["value"]["text"])

# 对话历史
log_key = (backend_name, session_id)
for msg in st.session_state.chat_log.get(log_key, []):
    with st.chat_message(msg["role"]):
        if "meta" in msg:
            st.markdown(f"**{msg['meta']}**")
        st.write(msg["content"])
        if msg.get("summary"):
            st.caption(f"记忆更新：{msg['summary']}")

# 输入
user_input = st.chat_input("说一句表达情绪的话试试")

if user_input:
    st.session_state.chat_log.setdefault(log_key, []).append(
        {"role": "user", "content": user_input}
    )
    with st.chat_message("user"):
        st.write(user_input)

    with st.chat_message("assistant"):
        placeholder = st.empty()
        badge, full, done_data = "", "", None
        try:
            resp = requests.post(
                base_url + "/chat/stream",
                json={"session_id": session_id, "text": user_input},
                stream=True,
                timeout=(10, 300),
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"

            event_type = None
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("event: "):
                    event_type = line[len("event: "):].strip()
                elif line.startswith("data: ") and event_type:
                    data = json.loads(line[len("data: "):])
                    if event_type == "sentiment":
                        badge = render_sentiment_badge(
                            data["sentiment"], data["intensity"]
                        )
                        placeholder.markdown(f"**{badge}**\n\n⏳ 正在回应…")
                    elif event_type == "delta":
                        full += data.get("text", "")
                        placeholder.markdown(f"**{badge}**\n\n{full}")
                    elif event_type == "done":
                        done_data = data
        except requests.exceptions.ConnectionError:
            st.error(f"无法连接 {backend_name} 后端，请先启动服务。")
        except Exception as e:
            st.error(f"请求失败：{e}")

        if done_data:
            # done 里的 session_id 应与当前一致（保留防御处理）
            if done_data["session_id"] != session_id:
                st.session_state.sessions[backend_name] = done_data["session_id"]
                new_key = (backend_name, done_data["session_id"])
                st.session_state.chat_log[new_key] = st.session_state.chat_log.pop(
                    log_key, []
                )
                log_key = new_key
            st.session_state.chat_log[log_key].append(
                {
                    "role": "assistant",
                    "content": done_data["reply"],
                    "meta": render_sentiment_badge(
                        done_data["sentiment"], done_data["intensity"]
                    ),
                    "summary": done_data.get("memory_summary"),
                }
            )
            st.rerun()  # 刷新长期记忆面板与对话区