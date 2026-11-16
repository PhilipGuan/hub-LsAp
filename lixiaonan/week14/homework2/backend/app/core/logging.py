"""日志模块：基于 loguru 的结构化日志（§6）。

关键链路（上传/发消息/消费/解析）均输出 task_id / doc_id 便于追踪；
同时拦截标准 logging（uvicorn / kafka 客户端的日志统一走 loguru）。
"""
import logging
import sys

from loguru import logger

_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)


class _InterceptHandler(logging.Handler):
    """把标准 logging 的日志转发给 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(level: str = "INFO", log_dir=None) -> None:
    logger.remove()
    logger.add(sys.stdout, level=level.upper(), format=_LOG_FORMAT)

    if log_dir is not None:
        logger.add(
            str(log_dir / "app_{time:YYYY-MM-DD}.log"),
            level=level.upper(),
            format=_LOG_FORMAT,
            rotation="00:00",
            retention="7 days",
            encoding="utf-8",
            enqueue=False,
        )

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "kafka", "kafka.client", "kafka.admin"):
        logging.getLogger(name).handlers = [_InterceptHandler()]
        logging.getLogger(name).propagate = False
