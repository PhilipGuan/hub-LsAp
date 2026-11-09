import asyncio
import base64
import logging
import time
import warnings
from contextlib import asynccontextmanager
from io import BytesIO

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import APITimeoutError, AsyncOpenAI, OpenAIError
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.config import MAX_IMAGE_BYTES, ROOT, load_settings
from app.recognition import analyze_image
from app.schemas import AnalysisResult

logger = logging.getLogger("hallway_risk")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
logger.propagate = False
STATIC = ROOT / "app" / "static"
FAILURE_MESSAGE = "分析失败，请重新拍摄或重试"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    async with AsyncOpenAI(
        api_key=settings.api_key, base_url=settings.base_url,
        timeout=settings.timeout, max_retries=0,
    ) as client:
        app.state.client = client
        yield


app = FastAPI(title="楼道消防风险识别", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def disable_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail)}},
    )


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": {
        "code": "INVALID_REQUEST", "message": "请选择一张图片后再提交",
    }})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


def prepare_image(data: bytes) -> str:
    """验证真实格式，校正方向，缩放后转为无 EXIF 的 JPEG。"""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                logger.info("image_received format=%s width=%d height=%d bytes=%d",
                            image.format, image.width, image.height, len(data))
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise HTTPException(415, "仅支持 JPEG、PNG、WebP 图片，不支持 HEIC")
                if image.width * image.height > 40_000_000:
                    raise HTTPException(400, "图片像素过大，请缩小后重新上传")
                image.load()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                rgba = image.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
                output = BytesIO()
                rgb.save(output, format="JPEG", quality=90)
                logger.info("image_prepared format=JPEG width=%d height=%d bytes=%d",
                            rgb.width, rgb.height, output.tell())
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, "图片损坏、格式不支持或无法解码，请重新选择图片") from None
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


@app.post("/api/analyze", response_model=AnalysisResult)
async def analyze(request: Request, file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_IMAGE_BYTES + 1)
    finally:
        await file.close()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "图片不能超过 10 MB")
    if not data:
        raise HTTPException(400, "图片为空，请重新选择图片")
    image_url = await run_in_threadpool(prepare_image, data)
    started = time.monotonic()
    try:
        # 限制整次模型调用耗时；SDK 的网络超时并不等于总耗时。
        async with asyncio.timeout(request.app.state.settings.timeout):
            result = await analyze_image(
                request.app.state.client, request.app.state.settings.model, image_url,
            )
    except (TimeoutError, APITimeoutError):
        logger.warning("analysis status=504 elapsed=%.2f", time.monotonic() - started)
        return JSONResponse(status_code=504, content={"error": {
            "code": "MODEL_TIMEOUT", "message": FAILURE_MESSAGE,
        }})
    except (OpenAIError, ValueError, TypeError) as exc:
        if isinstance(exc, ValidationError):
            # 只记录字段与错误类型，不能输出 errors() 中默认携带的原始输入。
            errors = [
                (str(error["loc"][0]) if error["loc"] and error["loc"][0] in {"risk_level", "reason"}
                 else "response", error["type"])
                for error in exc.errors(include_input=False, include_url=False, include_context=False)
            ]
            logger.warning("model_validation errors=%s", errors)
        logger.warning("analysis status=502 type=%s elapsed=%.2f",
                       type(exc).__name__, time.monotonic() - started)
        return JSONResponse(status_code=502, content={"error": {
            "code": "ANALYSIS_FAILED", "message": FAILURE_MESSAGE,
        }})
    logger.info("analysis status=200 elapsed=%.2f", time.monotonic() - started)
    return result
