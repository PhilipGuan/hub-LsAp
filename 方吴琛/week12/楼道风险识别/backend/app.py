"""楼道消防安全隐患识别 —— 后端 FastAPI 服务。

接口：
- GET  /                返回前端页面（frontend/index.html）
- GET  /api/providers   列出可用供应商及是否已配置 Key
- POST /api/analyze     上传图片 + 指定供应商，返回结构化风险结论

启动：
    cd backend
    uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from providers import PROVIDERS, analyze_image

app = FastAPI(title="楼道消防安全隐患识别")

# 允许跨域，方便前端独立部署或从手机访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

MAX_IMAGE_BYTES = 32 * 1024 * 1024  # 32MB


def sniff_mime(data: bytes, fallback: str) -> str:
    """根据文件头推断图片类型，浏览器没给 content-type 时兜底。"""
    if fallback and fallback.startswith("image/"):
        return fallback
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback or "image/jpeg"


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="前端页面不存在")
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.get("/api/providers")
def list_providers() -> dict:
    return {
        name: {
            "name": cfg["name"],
            "model": cfg["model"],
            "configured": bool(cfg["api_key"]),
        }
        for name, cfg in PROVIDERS.items()
    }


@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(...),
    provider: str = Form("qwen"),
) -> dict:
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"未知供应商: {provider}")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="图片过大（超过 32MB）")

    mime = sniff_mime(data, file.content_type or "")

    try:
        return analyze_image(data, mime, provider)
    except RuntimeError as e:  # 未配置 Key
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:  # 网络 / 接口错误
        raise HTTPException(status_code=502, detail=f"模型调用失败: {e}")
