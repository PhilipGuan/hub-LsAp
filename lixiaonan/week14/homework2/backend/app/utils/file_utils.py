"""文件工具：类型/大小/魔数校验（F1-2）、SHA-256 判重（F1-9）、ID 与文件名处理。"""
import hashlib
import uuid
from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import BizError, ErrorCode

PDF_MAGIC = b"%PDF-"


def validate_pdf(filename: str | None, data: bytes, settings: Settings) -> None:
    """上传校验（F1-2）：扩展名、非空、大小上限、PDF 魔数。"""
    name = filename or ""
    if not name.lower().endswith(".pdf"):
        raise BizError(ErrorCode.FILE_TYPE_INVALID, f"仅支持 PDF 文件: {name}")
    if not data:
        raise BizError(ErrorCode.FILE_TYPE_INVALID, f"文件为空: {name}")
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise BizError(
            ErrorCode.FILE_TOO_LARGE,
            f"文件 {name} 大小 {len(data) / 1024 / 1024:.1f}MB 超过上限 {settings.max_upload_size_mb}MB",
        )
    if not data.startswith(PDF_MAGIC):
        raise BizError(ErrorCode.FILE_TYPE_INVALID, f"文件 {name} 内容不是有效的 PDF")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_id() -> str:
    return uuid.uuid4().hex


def sanitize_filename(name: str | None) -> str:
    """去掉路径部分，仅保留文件名用于展示。"""
    return Path(name or "").name or "未命名.pdf"
