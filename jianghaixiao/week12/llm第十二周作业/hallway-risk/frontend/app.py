"""Streamlit 前端（骨架）。

启动：
    cd frontend
    python -m streamlit run app.py
"""
import requests
import streamlit as st

from PIL import Image

BACKEND_URL = "http://localhost:8000"

st.set_page_config(page_title="楼道消防隐患识别", page_icon="🏢", layout="centered")
st.title("🏢 楼道消防隐患识别")
st.caption("拍照或上传楼道照片，自动评估消防安全风险")

# ---------------------------------------------------------------- 颜色映射
LEVEL_STYLE = {
    "高": ("🔴", "#d32f2f"),
    "中": ("🟠", "#f57c00"),
    "低": ("🟡", "#fbc02d"),
    "无": ("🟢", "#388e3c"),
    "非楼道": ("⚪", "#757575"),
}


def render_result(data: dict):
    """渲染风险卡片。

    TODO:
    1. 取出 risk_level / risk_items / reason / suggestion / confidence
    2. 用 LEVEL_STYLE 显示颜色和图标
    3. st.markdown / st.progress 展示
    """
    st.info("TODO: 渲染风险卡片")


# ---------------------------------------------------------------- 输入区
tab_camera, tab_upload = st.tabs(["📷 拍照", "📁 上传图片"])

image_bytes = None

with tab_camera:
    camera_file = st.camera_input("对准楼道拍照")
    if camera_file is not None:
        image_bytes = camera_file.getvalue()

with tab_upload:
    uploaded_file = st.file_uploader("上传图片", type=["jpg", "jpeg", "png", "webp"])
    if uploaded_file is not None:
        image_bytes = uploaded_file.getvalue()

# ---------------------------------------------------------------- 预览 + 分析
if image_bytes:
    st.image(Image.open(image_bytes), caption="待识别图片", use_container_width=True)

    if st.button("🔍 开始识别", type="primary", use_container_width=True):
        with st.spinner("正在分析图片..."):
            # TODO: 调用后端
            # files = {"file": ("image.jpg", image_bytes, "image/jpeg")}
            # resp = requests.post(f"{BACKEND_URL}/v1/risk/analyze", files=files, timeout=60)
            # result = resp.json()
            # render_result(result["data"])
            st.warning("TODO: 调用后端 /v1/risk/analyze")