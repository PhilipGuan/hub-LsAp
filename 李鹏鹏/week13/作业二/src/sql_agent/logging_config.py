"""日志配置：默认输出到 stderr，避免污染 CLI 的 JSON 输出。"""

from __future__ import annotations

import logging
import os
import sys

DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def compact_text(text: object, limit: int = 2000) -> str:
    """把多行文本压成一行并截断，用于单行 key=value 日志。"""

    compact = " ".join(str(text).split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


def configure_logging(level: str | None = None) -> None:
    log_level = (level or os.getenv("SQL_AGENT_LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format=DEFAULT_LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    # 第三方 HTTP 日志过于嘈杂，只保留告警。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
