"""Markdown → 检索分块（保留页码、标题、图片引用）。"""

import re
from dataclasses import dataclass, field

from ..config import get_settings

PAGE_RE = re.compile(r"^<!--\s*page\s+(\d+)\s+of\s+(\d+)\s*-->$")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
IMAGE_URL_RE = re.compile(r"/static/parsed/[A-Za-z0-9_.\-/]+")
ALT_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    filename: str
    heading: str
    page: int
    content: str
    images: list[str] = field(default_factory=list)

    def to_es_doc(self, vector: list[float], index: str) -> dict:
        return {
            "_index": index,
            "_id": self.chunk_id,
            "_source": {
                "doc_id": self.doc_id,
                "chunk_id": self.chunk_id,
                "filename": self.filename,
                "heading": self.heading,
                "page": self.page,
                "content": self.content,
                "images": self.images,
                "content_vector": vector,
            },
        }


def _sliding(text: str, size: int, overlap: int) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    pieces: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > size:
            if buf:
                pieces.append(buf)
                buf = ""
            start = 0
            while start < len(para):
                pieces.append(para[start : start + size])
                start += max(size - overlap, 1)
            continue
        if len(buf) + len(para) + 2 <= size:
            buf = f"{buf}\n\n{para}" if buf else para
        else:
            pieces.append(buf)
            tail = buf[-overlap:] if overlap > 0 else ""
            buf = f"{tail}\n\n{para}" if tail else para
    if buf:
        pieces.append(buf)
    return pieces


def split_markdown(markdown: str, doc_id: str, filename: str) -> list[Chunk]:
    settings = get_settings()
    size = settings.chunk_size
    overlap = settings.chunk_overlap

    chunks: list[Chunk] = []
    page = 1
    heading = ""
    buf: list[str] = []
    idx = 0

    def flush() -> None:
        nonlocal buf, idx
        text = "\n".join(buf).strip()
        buf = []
        if not text:
            return
        for piece in _sliding(text, size, overlap):
            images = sorted(set(IMAGE_URL_RE.findall(piece)))
            plain = ALT_RE.sub(lambda m: m.group(1), piece)
            if not plain.strip():
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}:{idx}",
                    doc_id=doc_id,
                    filename=filename,
                    heading=heading[:120],
                    page=page,
                    content=plain[: size * 2],
                    images=images,
                )
            )
            idx += 1

    for raw in markdown.splitlines():
        line = raw.rstrip()
        m = PAGE_RE.match(line.strip())
        if m:
            flush()
            page = int(m.group(1))
            continue
        h = HEAD_RE.match(line)
        if h:
            flush()
            heading = h.group(2).strip()
            buf.append(line)
            continue
        buf.append(line)
        if sum(len(x) + 1 for x in buf) >= size:
            flush()
    flush()
    return chunks
