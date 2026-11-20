"""存储抽象（F1-3 / F1-8）：本地磁盘 / MinIO 双实现。

存储 key 约定（两种后端一致）：
- uploads/<doc_id>.pdf        PDF 原件
- markdown/<doc_id>.md        解析产物 Markdown
- images/<doc_id>/<seq>.<ext> 解析出的图片

本地模式：key 即 data_dir 下的相对路径，URL 由 /static 前缀直接提供。
MinIO 模式：对象存储于 bucket，URL 为预签名地址（有效期 7 天，作业场景够用）。
"""
import posixpath
from abc import ABC, abstractmethod
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from app.core.config import Settings
from app.core.exceptions import BizError, ErrorCode


class BaseStorage(ABC):
    @abstractmethod
    def save(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    def write_text(self, key: str, text: str) -> None:
        self.save(key, text.encode("utf-8"))

    def read_text(self, key: str) -> str:
        return self.read_bytes(key).decode("utf-8")

    @abstractmethod
    def url_for(self, key: str) -> str:
        """返回可通过 HTTP 访问的 URL。"""


class LocalStorage(BaseStorage):
    def __init__(self, settings: Settings):
        self.root = settings.data_path
        self.url_prefix = settings.static_url_prefix
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # 防路径穿越：key 必须落在数据根目录内
        if not path.is_relative_to(self.root.resolve()):
            raise BizError(ErrorCode.STORAGE_FAILED, f"非法存储路径: {key}", 500)
        return path

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise BizError(ErrorCode.NOT_FOUND, f"文件不存在: {key}", 404)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def url_for(self, key: str) -> str:
        return f"{self.url_prefix}/{posixpath.join(*[quote(p) for p in Path(key).parts])}"


class MinioStorage(BaseStorage):
    def __init__(self, settings: Settings):
        try:
            from minio import Minio
        except ImportError as exc:  # 延迟导入：选用 MinIO 时才需要安装
            raise BizError(
                ErrorCode.STORAGE_FAILED,
                "STORAGE_BACKEND=minio 需要安装 minio 包: pip install minio",
                500,
            ) from exc
        self.settings = settings
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
        if not self.client.bucket_exists(settings.minio_bucket):
            self.client.make_bucket(settings.minio_bucket)

    def save(self, key: str, data: bytes) -> None:
        import io

        self.client.put_object(
            self.settings.minio_bucket, key, io.BytesIO(data), length=len(data)
        )

    def read_bytes(self, key: str) -> bytes:
        from minio.error import S3Error

        response = None
        try:
            response = self.client.get_object(self.settings.minio_bucket, key)
            return response.read()
        except S3Error as exc:
            raise BizError(ErrorCode.NOT_FOUND, f"文件不存在: {key} ({exc})", 404) from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def exists(self, key: str) -> bool:
        from minio.error import S3Error

        try:
            self.client.stat_object(self.settings.minio_bucket, key)
            return True
        except S3Error:
            return False

    def url_for(self, key: str) -> str:
        return self.client.presigned_get_object(
            self.settings.minio_bucket, key, expires=timedelta(days=7)
        )


def build_storage(settings: Settings) -> BaseStorage:
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalStorage(settings)
    if backend == "minio":
        return MinioStorage(settings)
    raise BizError(ErrorCode.STORAGE_FAILED, f"未知存储后端: {settings.storage_backend}", 500)
