# -*- coding: utf-8 -*-
"""Markdown 切块：保留页号、表格与图片关联。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import CHUNK_SIZE, TEXT_MAX_BYTES
from .image_service import IMAGE_RE, resolve_local_image

PAGE_RE = re.compile(r"<!--\s*page\s+(\d+)\s+of\s+(\d+)\s*-->")
FOOTER_RE = re.compile(r"^第\s*\d+\s*页$")


@dataclass
class Chunk:
    text: str
    page: int = 0
    images: list[str] = field(default_factory=list)


def truncate_bytes(text: str, limit: int = TEXT_MAX_BYTES) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", errors="ignore")


def split_pages(markdown: str) -> list[tuple[int, str, int]]:
    """按 `<!-- page N of M -->` 拆页，返回 (页码, 内容, 总页数)。"""
    matches = list(PAGE_RE.finditer(markdown or ""))
    if not matches:
        return [(1, markdown or "", 1)]

    pages: list[tuple[int, str, int]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        pages.append((int(match.group(1)), markdown[start:end], int(match.group(2))))
    return pages


def _clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if FOOTER_RE.match(stripped):
            continue
        lines.append(line)
    return lines


def _blocks(lines: list[str]) -> list[str]:
    """空行分段；表格、图片行各自成块。"""
    blocks: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            block = "\n".join(buffer).strip()
            if block:
                blocks.append(block)
            buffer.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("|"):
            flush()
            blocks.append(stripped)
            continue
        if IMAGE_RE.match(stripped):
            flush()
            blocks.append(stripped)
            continue
        buffer.append(line)
    flush()
    return blocks


def _split_table(block: str, limit: int) -> list[str]:
    rows = [row for row in block.splitlines() if row.strip()]
    if len(rows) <= 2:
        return [block]
    header, separator, body = rows[0], rows[1], rows[2:]
    parts: list[str] = []
    current = [header, separator]
    for row in body:
        current.append(row)
        if len("\n".join(current)) >= limit:
            parts.append("\n".join(current))
            current = [header, separator]
    if len(current) > 2:
        parts.append("\n".join(current))
    return parts or [block]


def _split_long_text(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    pieces: list[str] = []
    buffer = ""
    for sentence in re.split(r"(?<=[。！？；.!?;])\s*", block):
        if not sentence:
            continue
        if buffer and len(buffer) + len(sentence) > limit:
            pieces.append(buffer)
            buffer = ""
        if len(sentence) > limit:
            for i in range(0, len(sentence), limit):
                pieces.append(sentence[i : i + limit])
        else:
            buffer += sentence
    if buffer:
        pieces.append(buffer)
    return [p for p in pieces if p.strip()]


def split_markdown(
    markdown: str,
    md_path: str | Path | None = None,
    chunk_size: int = CHUNK_SIZE,
    text_max_bytes: int = TEXT_MAX_BYTES,
) -> list[Chunk]:
    """把 MinerU 输出的 markdown 切成带页号的 chunk。"""
    base_dir = Path(md_path).parent if md_path else None
    chunks: list[Chunk] = []

    for page, content, _total in split_pages(markdown):
        prefix = f"[第{page}页] "
        budget = max(80, chunk_size - len(prefix))
        buffer: list[str] = []
        images: list[str] = []

        def flush() -> None:
            if not buffer:
                return
            body = "\n".join(buffer).strip()
            buffer.clear()
            if not body:
                return
            text = truncate_bytes(prefix + body, text_max_bytes)
            chunk_images = []
            for ref in images:
                real = resolve_local_image(ref, base_dir)
                if real is not None:
                    chunk_images.append(str(real))
            images.clear()
            chunks.append(Chunk(text=text, page=page, images=chunk_images))

        for block in _blocks(_clean_lines(content)):
            if block.startswith("|"):
                parts = _split_table(block, budget)
            elif IMAGE_RE.match(block):
                parts = [block]
            else:
                parts = _split_long_text(block, budget)

            for part in parts:
                if IMAGE_RE.match(part):
                    for ref in IMAGE_RE.findall(part):
                        images.append(ref[1])
                    if buffer and len("\n".join(buffer)) + len(part) > budget:
                        flush()
                    buffer.append(part)
                    if len("\n".join(buffer)) >= budget:
                        flush()
                    continue

                if buffer and len("\n".join(buffer)) + len(part) + 1 > budget:
                    flush()
                buffer.append(part)
                if len("\n".join(buffer)) >= budget:
                    flush()

        flush()

    # 去掉完全重复的 chunk，保留第一次出现
    seen: set[str] = set()
    unique: list[Chunk] = []
    for chunk in chunks:
        key = chunk.text
        if key in seen:
            continue
        seen.add(key)
        unique.append(chunk)
    return unique
