from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from backend.mimo import MiMoError, analyze_image
from backend.schemas import RiskAssessment


PROJECT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_DIR / "frontend"
load_dotenv(PROJECT_DIR / ".env")

MAX_IMAGE_BYTES = 15 * 1024 * 1024
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def sniff_image_type(data: bytes) -> str | None:
    for signature, mime_type in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime_type
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


app = FastAPI(title="楼道消防风险识别", version="1.0.0")
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.post("/api/analyze", response_model=RiskAssessment)
async def analyze(file: Annotated[UploadFile, File(...)]) -> RiskAssessment:
    try:
        image_bytes = await file.read(MAX_IMAGE_BYTES + 1)
    finally:
        await file.close()

    if not image_bytes:
        raise HTTPException(status_code=400, detail="图片文件为空。")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        limit_mb = MAX_IMAGE_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"图片不能超过 {limit_mb} MB。")

    mime_type = sniff_image_type(image_bytes)
    if mime_type is None:
        raise HTTPException(status_code=415, detail="请上传有效的 JPEG、PNG、GIF、WebP 或 BMP 图片。")

    try:
        return await run_in_threadpool(analyze_image, image_bytes, mime_type)
    except MiMoError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
