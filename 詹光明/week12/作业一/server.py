"""
楼道消防安全隐患识别 —— 后端 HTTP 服务（week12 作业一：后端部分）。

启动：
    pip install -r requirements.txt
    python server.py
    # 或：uvicorn server:app --host 0.0.0.0 --port 8000

接口：
    POST /api/analyze      上传图片（multipart/form-data，字段名 file），返回风险等级与依据
    GET  /api/health       服务与模型状态
    GET  /api/levels       风险分级说明（供前端图例使用）

前端页面：web/index.html，访问 / 即可打开，静态资源挂在 /static。
手机和电脑需在同一局域网，前端用 http://<电脑局域网IP>:8000 访问；
浏览器要求摄像头页面在 HTTPS 或 localhost 下才能调用 getUserMedia，
局域网手机调试可配置 HTTPS（见 README）：
    RISK_SSL_CERT=cert.pem RISK_SSL_KEY=key.pem python server.py
之后手机访问 https://<电脑局域网IP>:8000 并信任自签证书即可拍照。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from risk_analyzer import (
    RISK_CODES,
    RISK_LEVELS,
    RISK_PROMPT,
    RISK_RULES,
    RISK_SCORES,
    AnalyzerNotReady,
    create_analyzer,
    prepare_image,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("risk.server")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
UPLOAD_DIR = Path(os.getenv("RISK_UPLOAD_DIR", "").strip() or BASE_DIR / "data" / "uploads")
MAX_UPLOAD_BYTES = int(os.getenv("RISK_MAX_UPLOAD_MB", "20")) * 1024 * 1024
MAX_IMAGE_SIDE = int(os.getenv("RISK_MAX_IMAGE_SIDE", "2048"))
SAVE_UPLOADS = os.getenv("RISK_SAVE_UPLOADS", "1").strip() != "0"
PRELOAD = os.getenv("RISK_PRELOAD", "1").strip() != "0"

analyzer = create_analyzer()


def _preload_model() -> None:
    try:
        analyzer.load()
        logger.info("模型预加载完成")
    except AnalyzerNotReady as exc:
        logger.error("模型预加载失败，/api/analyze 将返回 503：%s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 后台线程加载模型：服务先起来，前端可先轮询 /api/health，避免首个请求长时间挂起
    if PRELOAD:
        threading.Thread(target=_preload_model, name="model-preload", daemon=True).start()
    yield


app = FastAPI(
    title="楼道消防安全隐患识别",
    description="上传楼道照片，调用多模态模型判断消防安全隐患等级。",
    version="1.0.0",
    lifespan=lifespan,
)

# 前端可能来自手机同源页面或本地其他端口，开发期放开跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index():
    index_file = WEB_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return {
        "service": "楼道消防安全隐患识别后端",
        "status": "running",
        "docs": "/docs",
        "endpoints": {
            "POST /api/analyze": "上传图片进行识别（multipart 字段名 file）",
            "GET /api/health": "服务与模型状态",
            "GET /api/levels": "风险分级说明",
        },
        "hint": "未找到 web/index.html，当前仅有后端接口可用。",
    }


@app.get("/api/health")
def health():
    info = analyzer.describe()
    if info.get("ready"):
        status = "ok"
    elif info.get("load_error"):
        status = "error"
    elif PRELOAD:
        status = "loading"
    else:
        status = "idle"
    return {
        "status": status,
        "ready": info.get("ready", False),
        "provider": info.get("provider"),
        "model": info.get("model"),
        "load_error": info.get("load_error"),
        "risk_levels": list(RISK_LEVELS),
    }


@app.get("/api/levels")
def levels():
    items = []
    for line in RISK_RULES.splitlines():
        level, _, description = line.partition("：")
        items.append(
            {
                "level": level,
                "code": RISK_CODES.get(level, "unknown"),
                "score": RISK_SCORES.get(level),
                "description": description,
            }
        )
    return {"levels": items, "prompt": RISK_PROMPT}


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), save_image: bool = Form(True)):
    """接收前端上传的图片，返回风险等级、隐患元素和判断依据。"""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"图片超过大小限制（{MAX_UPLOAD_BYTES // 1024 // 1024}MB）",
        )

    try:
        image = prepare_image(data, MAX_IMAGE_SIDE)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"无法解析图片，请上传 JPG/PNG/WEBP/BMP 格式的文件：{exc}",
        ) from exc

    started = time.perf_counter()
    try:
        # 推理是阻塞调用（且会串行占用 GPU），放到线程池避免阻塞事件循环
        result = await asyncio.to_thread(analyzer.analyze, image)
    except AnalyzerNotReady as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("推理失败")
        raise HTTPException(status_code=500, detail=f"推理失败：{exc}") from exc

    image_path = None
    if save_image and SAVE_UPLOADS:
        try:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.jpg"
            dest = UPLOAD_DIR / filename
            await asyncio.to_thread(image.save, dest, "JPEG", quality=92)
            image_path = dest.relative_to(BASE_DIR).as_posix()
        except OSError as exc:
            # 保存失败不影响识别结果
            logger.warning("保存上传图片失败：%s", exc)

    payload = result.to_dict()
    payload.update(
        {
            "request_id": uuid.uuid4().hex,
            "filename": Path(file.filename or "").name,
            "width": image.width,
            "height": image.height,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "image_path": image_path,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    logger.info(
        "识别完成 filename=%s level=%s elapsed=%sms",
        payload["filename"],
        result.risk_level,
        payload["elapsed_ms"],
    )
    return payload


if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host=os.getenv("RISK_HOST", "0.0.0.0"),
        port=int(os.getenv("RISK_PORT", "8000")),
        ssl_certfile=os.getenv("RISK_SSL_CERT") or None,
        ssl_keyfile=os.getenv("RISK_SSL_KEY") or None,
    )
