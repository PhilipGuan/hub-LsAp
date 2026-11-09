"""FastAPI application: static frontend + one image-analysis endpoint."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import (
    DEFAULT_PROVIDER,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_MB,
    MAX_IMAGE_SIDE,
    PROVIDERS,
    get_provider,
    public_providers,
)
from .imaging import ImageValidationError, prepare_image
from .vision import AnalysisError, analyze_image

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"

app = FastAPI(
    title="楼道哨兵 · 消防安全隐患识别",
    description=(
        "上传或拍摄楼道照片，调用 Qwen-VL / DeepSeek Flash 视觉大模型，"
        "输出五级消防安全风险研判。"
    ),
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.url.path == "/api/analyze" and request.method == "POST":
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES + 1024 * 1024:
            return JSONResponse(
                status_code=413,
                content={"detail": f"图片超过 {MAX_UPLOAD_MB} MB，请压缩后重试。"},
            )

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(self), microphone=(), geolocation=()"
    )
    path = request.url.path
    docs_path = path == "/openapi.json" or path.startswith(("/docs", "/redoc"))
    if not docs_path:
        # Swagger UI / ReDoc load their assets from a CDN, so the strict page
        # CSP only applies to the application itself.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "connect-src 'self'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'",
        )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=500, detail="前端页面缺失。")
    return FileResponse(INDEX_FILE, headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health", summary="服务健康检查")
async def health() -> dict:
    configured = [p.id for p in PROVIDERS.values() if p.configured]
    return {
        "status": "ok",
        "default_provider": DEFAULT_PROVIDER,
        "configured_providers": configured,
        "models_ready": bool(configured),
    }


@app.get("/api/providers", summary="可用的视觉模型")
async def providers() -> dict:
    items = public_providers()
    return {
        "providers": items,
        "default": next((item["id"] for item in items if item["default"]), None),
        "any_configured": any(item["configured"] for item in items),
    }


@app.post("/api/analyze", summary="识别楼道照片的消防安全风险")
async def analyze(
    file: UploadFile = File(..., description="楼道照片（JPG / PNG / WebP / HEIC）"),
    provider: str = Form(default=DEFAULT_PROVIDER, description="qwen 或 deepseek"),
) -> JSONResponse:
    try:
        provider_config = get_provider(provider)
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"未知的模型标识：{provider}。可选：{', '.join(PROVIDERS)}",
        ) from exc

    if not provider_config.configured:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{provider_config.label} 尚未配置 API Key。"
                "请在项目根目录的 .env 中填写后重启服务，详见 README。"
            ),
        )

    content_type = (file.content_type or "").lower()
    if content_type and not (
        content_type.startswith("image/")
        or content_type == "application/octet-stream"
    ):
        raise HTTPException(status_code=415, detail="请上传图片文件。")

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if not raw:
        raise HTTPException(status_code=400, detail="上传的图片为空。")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"图片超过 {MAX_UPLOAD_MB} MB，请压缩后重试。",
        )

    try:
        jpeg = prepare_image(raw, max_side=MAX_IMAGE_SIDE)
    except ImageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = await run_in_threadpool(analyze_image, jpeg, provider_config)
    except AnalysisError as exc:
        logger.info("Analysis failed via %s: %s", provider_config.id, exc.message)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    response = JSONResponse(result)
    response.headers["X-Analysis-Id"] = result["analysis_id"]
    return response
