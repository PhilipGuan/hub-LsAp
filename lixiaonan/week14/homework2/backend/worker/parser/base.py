"""解析器抽象基类（F1-7，§6 可维护性：解析器可替换）。

ParseResult 携带：
- markdown：原文 Markdown（图片引用为解析器内部的相对路径 source_ref）
- images：  图片二进制 + source_ref + 页码（Worker 负责落盘并把 source_ref 改写为可访问 URL）
- page_count：页数（无法确定时为 None）
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ParseError(Exception):
    """解析失败（解析器内部错误 / 超时 / 产物缺失等）。"""


@dataclass
class ParsedImage:
    data: bytes
    ext: str              # 不含点，如 png / jpg
    source_ref: str       # Markdown 中的原始引用路径，如 images/xxx.jpg
    page_no: int | None = None   # 从 0 计的页索引；不确定为 None


@dataclass
class ParseResult:
    markdown: str
    images: list[ParsedImage] = field(default_factory=list)
    page_count: int | None = None


class BaseParser(ABC):
    name: str = "base"

    @abstractmethod
    def parse(self, pdf_path: str) -> ParseResult:
        """解析指定 PDF 文件，返回 Markdown 与图片列表。抛 ParseError 表示解析失败。"""
