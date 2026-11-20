"""分块器（F1-8）：把解析得到的 Markdown 切分为文本块 / 图片块。

规则：
- 独立成行的 ![alt](url) 视为图片块（image_url 即 URL 本身）
- # 标题开起新块；空行在块长度达到 min_chars 时收块；达到 max_chars 强制收块
- 识别「第 N 页」标题，为后续块补充页码（pymupdf 解析器按页产出该标题）
"""
import re
from dataclasses import dataclass


IMAGE_LINE_RE = re.compile(r"^!\[[^\]]*\]\(([^)\s]+)\)\s*$")
PAGE_HEADING_RE = re.compile(r"^#{1,6}\s*第\s*(\d+)\s*页\s*$")


@dataclass
class ChunkOut:
    seq: int
    chunk_type: str          # TEXT / IMAGE
    content: str
    image_url: str | None = None
    page_no: int | None = None   # 从 1 计；无法确定时为 None


def split_markdown(md: str, min_chars: int = 300, max_chars: int = 1200) -> list[ChunkOut]:
    chunks: list[ChunkOut] = []
    buf: list[str] = []
    buf_len = 0
    page_no: int | None = None

    def flush() -> None:
        nonlocal buf, buf_len
        text = "\n".join(buf).strip()
        if text:
            chunks.append(ChunkOut(
                seq=len(chunks), chunk_type="TEXT", content=text, page_no=page_no,
            ))
        buf, buf_len = [], 0

    for raw_line in md.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        img_match = IMAGE_LINE_RE.match(stripped)
        if img_match:
            flush()
            url = img_match.group(1)
            chunks.append(ChunkOut(
                seq=len(chunks), chunk_type="IMAGE",
                content=url, image_url=url, page_no=page_no,
            ))
            continue

        page_match = PAGE_HEADING_RE.match(stripped)
        if page_match:
            flush()
            page_no = int(page_match.group(1))
            continue

        if stripped.startswith("#"):
            flush()
            buf, buf_len = [line], len(stripped)
            continue

        if not stripped:
            if buf and buf_len >= min_chars:
                flush()
            continue

        buf.append(line)
        buf_len += len(stripped)
        if buf_len >= max_chars:
            flush()

    flush()
    return chunks
