"""楼道消防安全隐患识别 —— FastAPI 服务入口。

启动：
    python -m uvicorn main:app --host 0.0.0.0 --port 8000
手机通过 HTTPS 访问请使用同目录下的 run.sh。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from analyzer import AnalyzerError, analyze_image
from config import BASE_DIR, settings

logger = logging.getLogger("uvicorn.error")

# 生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "模型=%s | 演示模式=%s | API Key=%s | 输出上限=%s tokens",
        settings.model,
        "开" if settings.mock_mode else "关",
        "已配置" if settings.key_configured else "未配置",
        settings.max_output_tokens,
    )
    yield


app = FastAPI(
    title="楼道消防安全隐患识别",
    description="上传或拍摄楼道照片，调用多模态大模型判定消防安全隐患等级。",
    version="1.0.0",
    lifespan=lifespan,
)

STATIC_DIR = BASE_DIR / "static"
# 把 static 文件夹挂载到 /static 路径下，用于提供 CSS、JS、图片等静态资源
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    """前端用它展示当前模型与运行状态。"""
    return {
        "status": "ok",
        "model": settings.model,
        "base_url": settings.base_url,
        "mock": settings.mock_mode,
        "key_configured": settings.key_configured,
        "max_image_mb": settings.max_image_mb,
    }


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    """接收一张图片，返回风险等级、置信度、判定依据和整改建议。"""
    if file.content_type and not (
        file.content_type.startswith("image/") or file.content_type == "application/octet-stream"
    ):
        return _error("只支持上传图片文件。", 415)

    data = await file.read() # 异步读取文件内容
    try:
        # run_in_threadpool： 把同步的 analyze_image 丢到线程池里执行，这样主线程就能继续处理其他 HTTP 请求，保证了高并发下的性能
        result = await run_in_threadpool(analyze_image, data)
    except AnalyzerError as exc:
        return _error(exc.message, exc.status_code)
    except Exception:  # noqa: BLE001
        # 不把堆栈暴露给前端，服务端日志里仍然可见
        import logging

        logging.getLogger("uvicorn.error").exception("分析图片时发生未预期错误")
        return _error("服务内部错误，请稍后重试。", 500)

    return JSONResponse(result)


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status_code)
