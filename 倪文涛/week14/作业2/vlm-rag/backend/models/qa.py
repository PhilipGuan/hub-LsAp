from base64 import b64encode
from dataclasses import dataclass
from typing import Literal


MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5_000_000
MAX_QUESTION_CHARS = 4000


@dataclass(frozen=True)
class Image:
    data: bytes
    mime_type: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "Image":
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("图片不能为空或大于 5 MB。")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return cls(data, "image/png")
        if data.startswith(b"\xff\xd8\xff"):
            return cls(data, "image/jpeg")
        raise ValueError("请使用 PNG 或 JPEG 图片。")

    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{b64encode(self.data).decode('ascii')}"


@dataclass(frozen=True)
class Question:
    text: str
    images: list[Image]


@dataclass(frozen=True)
class TextBlock:
    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ImageBlock:
    image_id: str
    image_url: str
    type: Literal["image"] = "image"


@dataclass(frozen=True)
class Answer:
    blocks: list[TextBlock | ImageBlock]
