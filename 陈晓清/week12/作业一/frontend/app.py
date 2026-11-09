"""楼道消防安全隐患识别 - Streamlit 前端"""
import html
import os

import requests
import streamlit as st

DEFAULT_API = os.getenv("API_BASE", "http://127.0.0.1:8000")

# 等级 -> (中文名, 主色, 背景色, 图标)
RISK_STYLE = {
    "high": ("高风险", "#d32f2f", "#fdecea", "🔴"),
    "medium": ("中风险", "#ef6c00", "#fff4e5", "🟠"),
    "low": ("低风险", "#f9a825", "#fffbe6", "🟡"),
    "none": ("无风险", "#2e7d32", "#eaf7ea", "🟢"),
    "not_corridor": ("非楼道", "#616161", "#f2f2f2", "⚪"),
    "unknown": ("识别失败", "#616161", "#f2f2f2", "❔"),
}

st.set_page_config(page_title="楼道消防隐患识别", page_icon="🧯", layout="centered")


# ----------------------------- 工具函数 -----------------------------
def check_backend(api_base: str) -> tuple[bool, str]:
    try:
        resp = requests.get(f"{api_base}/health", timeout=3)
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            return True, "后端在线"
        return False, f"后端返回异常：HTTP {resp.status_code}"
    except requests.RequestException as exc:
        return False, f"连不上后端：{exc.__class__.__name__}"


def call_analyze(api_base: str, filename: str, raw: bytes, mime: str) -> dict:
    resp = requests.post(
        f"{api_base}/api/v1/analyze",
        files={"file": (filename, raw, mime)},
        timeout=(5, 120),
    )
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(f"后端返回非 JSON 内容：HTTP {resp.status_code}")

    if resp.status_code != 200 or body.get("code") != 0:
        raise RuntimeError(body.get("message") or f"请求失败：HTTP {resp.status_code}")
    return body["data"]


def render_result(data: dict) -> None:
    level = data.get("risk_level", "unknown")
    name, color, bg, icon = RISK_STYLE.get(level, RISK_STYLE["unknown"])

    objects = data.get("objects") or []
    chips = "".join(
        f'<span style="display:inline-block;background:#ffffff;border:1px solid {color};'
        f'color:{color};border-radius:12px;padding:2px 10px;margin:4px 6px 0 0;'
        f'font-size:13px">{html.escape(str(o))}</span>'
        for o in objects
    )
    chips_html = f'<div style="margin-top:10px">识别物体：{chips}</div>' if chips else ""
    reason = html.escape(data.get("reason") or "—")
    suggestion = html.escape(data.get("suggestion") or "—")
    elapsed = data.get("elapsed_ms", 0)

    st.markdown(
        f"""
        <div style="border-left:6px solid {color};background:{bg};border-radius:10px;
                    padding:16px 18px;margin-top:12px">
            <div style="font-size:24px;font-weight:700;color:{color}">{icon} {html.escape(name)}</div>
            <div style="margin-top:10px;color:#333;line-height:1.7">
                <b>判定依据：</b>{reason}<br/>
                <b>处置建议：</b>{suggestion}
            </div>
            {chips_html}
            <div style="margin-top:12px;font-size:12px;color:#888">后端耗时 {elapsed} ms</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ----------------------------- 侧边栏 -----------------------------
with st.sidebar:
    st.subheader("设置")
    api_base = st.text_input("后端地址", value=DEFAULT_API).rstrip("/")
    if st.button("检测后端连接", width="stretch"):
        ok, msg = check_backend(api_base)
        (st.success if ok else st.error)(msg)

    st.divider()
    st.caption(
        "手机访问提示：浏览器要求 HTTPS 才能调用摄像头。"
        "用 localhost 或内网穿透（如 ngrok）打开本页面；"
        "直接走局域网 IP 时请改用「上传图片」。"
    )
    st.caption("后端地址也应填电脑的局域网 IP，例如 http://192.168.1.10:8000")


# ----------------------------- 页面主体 -----------------------------
st.title("🧯 楼道消防安全隐患识别")
st.caption("拍一张楼道照片，自动判断是否存在消防隐患及危险程度。")

tab_camera, tab_upload = st.tabs(["📷 拍照", "🖼️ 上传图片"])

with tab_camera:
    camera_file = st.camera_input("调用摄像头拍摄楼道", key="camera")

with tab_upload:
    upload_file = st.file_uploader(
        "选择一张图片", type=["jpg", "jpeg", "png", "webp"], key="upload"
    )

image_file = camera_file or upload_file

if image_file is not None:
    st.image(image_file, caption="待识别图片", width=360)

col_run, col_clear = st.columns([1, 1])
run = col_run.button(
    "开始识别", type="primary", disabled=image_file is None, width="stretch"
)
if col_clear.button("清空结果", width="stretch"):
    st.session_state.pop("result", None)
    st.rerun()

if run and image_file is not None:
    with st.spinner("正在调用大模型识别，请稍候…"):
        try:
            st.session_state["result"] = call_analyze(
                api_base,
                image_file.name,
                image_file.getvalue(),
                image_file.type or "image/jpeg",
            )
        except requests.Timeout:
            st.error("请求超时，请检查网络或稍后重试。")
        except requests.ConnectionError:
            st.error(f"连不上后端 {api_base}，请确认服务已启动。")
        except (RuntimeError, requests.RequestException) as exc:
            st.error(str(exc))
        except KeyError:
            st.error("后端返回格式异常，缺少 data 字段。")

if "result" in st.session_state:
    render_result(st.session_state["result"])
    st.caption("⚠️ 结果由大模型生成，仅供参考，实际处置请以现场核查为准。")
