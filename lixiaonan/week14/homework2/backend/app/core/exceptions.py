"""统一业务异常与全局异常处理器（§5 统一响应结构）。

HTTP 状态码表达传输层语义，响应体 code 表达业务错误码：
{ "code": <int>, "message": <str>, "data": null }
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger


class ErrorCode:
    PARAM_ERROR = 1001
    FILE_TOO_LARGE = 1002
    FILE_TYPE_INVALID = 1003
    TOO_MANY_FILES = 1004
    DUPLICATE_FORBIDDEN = 1005
    NOT_FOUND = 1006
    KAFKA_SEND_FAILED = 2001
    STORAGE_FAILED = 2002
    PARSE_FAILED = 2003
    INTERNAL_ERROR = 5000


class BizError(Exception):
    """业务异常：路由层抛出后由全局处理器统一转为响应体。"""

    def __init__(self, code: int, message: str, http_status: int = 400):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


def _resp(code: int, message: str, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"code": code, "message": message, "data": None},
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError):
        logger.warning(f"业务异常 code={exc.code} message={exc.message} path={request.url.path}")
        return _resp(exc.code, exc.message, exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errors = "; ".join(
            f"{'.'.join(str(loc) for loc in err.get('loc', []))}: {err.get('msg', '')}"
            for err in exc.errors()[:5]
        )
        return _resp(ErrorCode.PARAM_ERROR, f"参数错误: {errors}", 400)

    @app.exception_handler(Exception)
    async def internal_handler(request: Request, exc: Exception):
        logger.exception(f"未处理异常 path={request.url.path}: {exc}")
        return _resp(ErrorCode.INTERNAL_ERROR, f"服务内部错误: {exc}", 500)
