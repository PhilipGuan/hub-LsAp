"""FastAPI 入口：/api/analyze 图片风险识别，/api/health 健康检查。

启动（在项目根目录 homework1/ 下执行）：
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config
from .schemas import AnalyzeRequest, AnalyzeResponse
from .vision_client import VisionError, analyze_image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("backend")

app = FastAPI(title="楼道消防隐患识别", version="0.1.0")

# 允许本地前端开发跨域调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    """服务与配置自检。"""
    return {
        "status": "ok",
        "model": config.MODEL,
        "base_url": config.BASE_URL,
        "api_key_configured": bool(config.API_KEY),
    }


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    """识别一张图片的楼道消防隐患等级。"""
    try:
        result = analyze_image(req.image)
    except VisionError as exc:
        if exc.raw:
            logger.warning("模型原始输出: %s", exc.raw[:500])
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    logger.info(
        "识别结果: %s (%.2f) %dms",
        result["risk_level"],
        result["confidence"],
        result["elapsed_ms"],
    )
    return result


# frontend/ 目录存在时自动挂载静态页（M2 之后生效）
FRONTEND_DIR = config.BASE_DIR / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
