"""楼道消防隐患识别后端服务（FastAPI）。

启动：
    cd backend
    python main.py
默认地址 http://0.0.0.0:8000，同时托管前端页面。
"""
import os

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import config
import vision

app = FastAPI(title="楼道消防隐患识别", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _sniff_mime(data: bytes) -> str:
    """根据文件头推断图片类型（前端上传时 content_type 可能缺失/为 octet-stream）。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "model": config.DEEPSEEK_MODEL,
        "api_key_configured": bool(config.DEEPSEEK_API_KEY),
    }


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "图片为空")

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_TYPES:
        sniffed = _sniff_mime(data)
        if not sniffed:
            raise HTTPException(400, f"不支持的图片类型：{content_type or '未知'}")
        content_type = sniffed

    if len(data) > config.MAX_IMAGE_BYTES:
        raise HTTPException(
            400, f"图片过大（{len(data)} 字节，上限 {config.MAX_IMAGE_BYTES} 字节）"
        )

    try:
        result = vision.analyze_image(data, content_type)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"识别失败：{e}")

    return {"ok": True, **result}


# 托管前端静态资源（须在 API 路由之后注册，避免覆盖 /api/*）
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)
