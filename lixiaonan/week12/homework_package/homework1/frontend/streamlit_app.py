"""楼道消防隐患识别 - Streamlit 前端。

运行（在项目根目录 homework1/ 下）:
    streamlit run frontend/streamlit_app.py

默认连接 http://127.0.0.1:8000 的 FastAPI 后端（backend/main.py），
可通过环境变量 BACKEND_URL 或侧边栏输入框修改。

注意：st.camera_input 与所有网页拍照一样要求安全上下文，
电脑端 localhost 可直接用；手机端需 HTTPS（可用 ngrok 等穿透），
手机上也可直接用"上传图片"调起系统相机。
"""

import base64
import mimetypes
import os
import time

import httpx
import streamlit as st

st.set_page_config(page_title="楼道消防隐患识别", page_icon="🔥", layout="centered")

DEFAULT_BACKEND = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

# risk_level -> (中文标签, 主题色, 图标)
RISK_STYLE = {
    "high": ("高风险", "#e5484d", "🔴"),
    "medium": ("中风险", "#f76b15", "🟠"),
    "low": ("低风险", "#ffb224", "🟡"),
    "none": ("无风险", "#46a758", "🟢"),
    "not_corridor": ("非楼道", "#8f8f8f", "⚪"),
}

HISTORY_LIMIT = 10


# ---------------------------------------------------------------- 后端调用

def to_data_url(file) -> str:
    mime = file.type or mimetypes.guess_type(file.name)[0] or "image/jpeg"
    b64 = base64.b64encode(file.getvalue()).decode()
    return f"data:{mime};base64,{b64}"


def call_analyze(backend: str, file) -> dict | None:
    """调用后端 /api/analyze，失败时在页面提示并返回 None。"""
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{backend}/api/analyze", json={"image": to_data_url(file)})
    except httpx.HTTPError as exc:
        st.error(f"无法连接后端 {backend}：{exc}")
        return None
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        st.error(f"识别失败 HTTP {resp.status_code}：{detail}")
        return None
    return resp.json()


def check_health(backend: str):
    try:
        resp = httpx.get(f"{backend}/api/health", timeout=5)
        resp.raise_for_status()
        st.session_state.health = resp.json()
        st.session_state.health_error = None
    except Exception as exc:
        st.session_state.health = None
        st.session_state.health_error = str(exc)


# ---------------------------------------------------------------- 展示组件

def render_result(r: dict):
    label, color, icon = RISK_STYLE.get(r["risk_level"], ("未知", "#8f8f8f", "❓"))
    st.markdown(
        f"""
        <div style="border-left:6px solid {color}; background:{color}18;
                    border-radius:10px; padding:16px 20px; margin:8px 0;">
          <div style="font-size:28px; font-weight:700; color:{color};">{icon} {label}</div>
          <div style="margin-top:8px; font-size:15px;">{r.get("reason") or "—"}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("置信度", f"{r.get('confidence', 0):.0%}")
    c2.metric("耗时", f"{r.get('elapsed_ms', 0) / 1000:.1f}s")
    c3.metric("模型", r.get("model", "-"))
    if r.get("elements"):
        st.markdown(" ".join(f"`{e}`" for e in r["elements"]))
    if r.get("suggestion"):
        st.info(f"处置建议：{r['suggestion']}")


def render_history():
    history = st.session_state.get("history", [])
    with st.expander(f"📜 识别历史（{len(history)}）"):
        if not history:
            st.caption("暂无记录")
            return
        for h in reversed(history):
            label, color, icon = RISK_STYLE.get(
                h["result"].get("risk_level", ""), ("未知", "#8f8f8f", "❓")
            )
            cols = st.columns([1, 4])
            cols[0].image(h["thumb"], width=96)
            cols[1].markdown(
                f"**{icon} {label}** · {h['name']} · {h['time']}\n\n"
                f"{h['result'].get('reason') or '—'}"
            )
        if st.button("清空历史", key="clear_history"):
            st.session_state.history = []
            st.rerun()


# ---------------------------------------------------------------- 页面结构

st.title("🔥 楼道消防隐患识别")
st.caption("拍照或上传楼道照片，AI 实时判定消防隐患等级：高风险 / 中风险 / 低风险 / 无风险 / 非楼道")

# 侧边栏：服务状态
with st.sidebar:
    st.header("⚙️ 服务状态")
    backend = st.text_input("后端地址", value=DEFAULT_BACKEND)
    if st.button("检查连接", use_container_width=True):
        check_health(backend)
    if "health" not in st.session_state and "health_error" not in st.session_state:
        check_health(backend)

    health = st.session_state.get("health")
    error = st.session_state.get("health_error")
    if health:
        if health.get("api_key_configured"):
            st.success(f"已连接 · {health.get('model')}")
        else:
            st.warning("已连接，但后端 API_KEY 未配置")
        st.caption(f"接口：{health.get('base_url')}")
    elif error:
        st.error(f"后端不可达：{error}")
    st.divider()
    st.caption("手机端提示：网页拍照需 HTTPS，可改用「上传图片」调起系统相机。")

# 主区域：拍照 / 上传
tab_cam, tab_upload = st.tabs(["📷 拍照识别", "🖼️ 上传图片"])
image_file = None
with tab_cam:
    cam = st.camera_input("对准楼道拍照", key="cam")
    if cam is not None:
        image_file = cam
with tab_upload:
    up = st.file_uploader(
        "选择楼道照片", type=["jpg", "jpeg", "png", "webp", "bmp"], key="up"
    )
    if up is not None:
        image_file = up

if image_file is None:
    st.info("请先拍照或上传一张楼道照片")
    render_history()
    st.stop()

st.image(image_file, use_container_width=True)

if st.button("🔍 开始识别", type="primary", use_container_width=True):
    with st.spinner("AI 正在识别，请稍候…"):
        result = call_analyze(backend, image_file)
    if result is not None:
        st.session_state.last_result = result
        history = st.session_state.setdefault("history", [])
        history.append(
            {
                "time": time.strftime("%H:%M:%S"),
                "name": image_file.name,
                "thumb": image_file.getvalue(),
                "result": result,
            }
        )
        del history[:-HISTORY_LIMIT]

if "last_result" in st.session_state:
    render_result(st.session_state.last_result)

render_history()
