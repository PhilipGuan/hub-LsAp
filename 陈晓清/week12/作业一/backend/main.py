"""FastAPI 入口：健康检查 + 图片风险识别"""
import logging
import time

from fastapi import File, FastAPI, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import OPENAI_API_KEY, OPENAI_MODEL
from .llm import LLMError, analyze_image
from .schemas import AnalyzeResponse, RiskData
from .vision import ImageError, to_data_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("corridor-risk")

app = FastAPI(title="楼道消防安全隐患识别 API", version="1.0.0")

# Demo 用全放开，生产需收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class BizError(Exception):
    def __init__(self, code: int, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status


@app.exception_handler(BizError)
async def biz_error_handler(request: Request, exc: BizError):
    logger.warning("业务异常 %s: %s", exc.code, exc.message)
    return JSONResponse(
        status_code=exc.status,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    logger.warning("参数校验失败: %s", exc.errors())
    return JSONResponse(
        status_code=400,
        content={"code": 4001, "message": "缺少必填参数 file，请以 multipart/form-data 上传图片", "data": None},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception("未处理异常")
    return JSONResponse(
        status_code=500,
        content={"code": 5000, "message": f"服务器内部错误：{exc}", "data": None},
    )


if not OPENAI_API_KEY:
    logger.warning("未读取到 OPENAI_API_KEY，请检查项目根目录的 .env")
logger.info("模型：%s", OPENAI_MODEL)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/analyze", response_model=AnalyzeResponse)
def analyze(file: UploadFile = File(...)):
    started = time.perf_counter()

    try:
        data_url = to_data_url(file.file.read())
    except ImageError as exc:
        raise BizError(4001, str(exc), status=400)

    try:
        result = analyze_image(data_url)
    except LLMError as exc:
        raise BizError(5001, f"模型调用失败：{exc}", status=502)

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(
        "识别完成 level=%s objects=%s %dms",
        result["risk_level"],
        result["objects"],
        elapsed,
    )

    if result["risk_level"] == "unknown":
        raise BizError(5002, f"{result['reason']}，请重拍", status=502)

    return AnalyzeResponse(
        data=RiskData(
            risk_level=result["risk_level"],
            risk_name=result["risk_name"],
            objects=result["objects"],
            reason=result["reason"],
            suggestion=result["suggestion"],
            elapsed_ms=elapsed,
        )
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
