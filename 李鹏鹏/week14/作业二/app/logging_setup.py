"""日志与控制台编码。

Windows 控制台默认 GBK，中文日志/emoji 容易乱码甚至抛 UnicodeEncodeError，
这里统一把标准输出切到 UTF-8，并给出统一的日志格式。
"""
from __future__ import annotations

import contextlib
import logging
import sys


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding in {"utf8", "utf_8"}:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def setup_logging(level: int = logging.INFO) -> None:
    setup_console()
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    # 第三方库降噪
    for noisy in ("kafka", "pdfminer", "httpx", "urllib3", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
