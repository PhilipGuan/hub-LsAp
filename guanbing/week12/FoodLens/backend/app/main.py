"""FoodLens FastAPI 应用。"""

from __future__ import annotations

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .nutrition.database import get_database
from .orchestrator import analyze, recompute
from .schemas import AnalyzeResponse, RecomputeRequest

settings = get_settings()

app = FastAPI(title="FoodLens API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"https://.*\.trycloudflare\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {
        "provider": settings.vision_provider,
        "status": "ok",
        "nutrition_count": len(get_database()),
    }


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def api_analyze(
    image: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    mode: str = Form(default="meal"),
    locale: str = Form(default="zh-CN"),
) -> AnalyzeResponse:
    normalized_mode = "label" if mode == "label" else "meal"
    image_bytes: bytes | None = None
    mime = "image/jpeg"
    if image is not None:
        image_bytes = await image.read()
        if image.content_type:
            mime = image.content_type

    return await analyze(
        settings,
        mode=normalized_mode,
        image_bytes=image_bytes,
        text=text,
        locale=locale,
        mime=mime,
    )


@app.post("/api/recompute", response_model=AnalyzeResponse)
async def api_recompute(request: RecomputeRequest) -> AnalyzeResponse:
    return recompute(request)
