import logging
import re
from pathlib import Path
from traceback import walk_tb


BACKEND_DIR = Path(__file__).resolve().parent


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # SDK request URLs may contain deployment information. Log application events.
    for name in ("httpx", "httpcore", "elastic_transport", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)


def error_context(exc: Exception) -> dict:
    """提取可共享的错误元数据，避免记录异常正文、请求体和凭据。"""
    details = {"type": type(exc).__name__}
    for key in ("status_code", "errno", "returncode", "timeout"):
        value = getattr(exc, key, None)
        if isinstance(value, (int, float)):
            details[key] = value

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {})
    trace_id = headers.get("x-siliconcloud-trace-id") or headers.get("x-request-id")
    if trace_id and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", trace_id):
        details["trace_id"] = trace_id

    # 保留最内层项目代码的位置，方便在服务器日志里定位。
    for frame, line in walk_tb(exc.__traceback__):
        file = Path(frame.f_code.co_filename)
        if file.is_relative_to(BACKEND_DIR):
            details["location"] = f"backend/{file.relative_to(BACKEND_DIR)}:{line}"
    return details
