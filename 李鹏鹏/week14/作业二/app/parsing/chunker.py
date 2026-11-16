"""内容块 → 可检索 chunk。

切块策略
--------
* **正文**：按段落累积，超过 ``CHUNK_SIZE`` 时在句末切分，保留 ``CHUNK_OVERLAP``
  的重叠，避免答案被切断；每个 chunk 记录起止页码与所属章节。
* **图片**：单独成块，正文写成「图注 + 所在章节 + 图前/图后正文」，
  这样用户用文字就能检索到图（多模态 RAG 的关键一步），
  同时保留图片 URL 供回答时图文混排。
* **表格**：转成 Markdown 表格后作为独立块，并带上表注。
"""
from __future__ import annotations

import re
from typing import Any

from ..config import settings
from .base import Block, ParsedDocument

_HEADING_NOISE = re.compile(r"^[\s#*\d.、()（）一二三四五六七八九十]+")
_SENTENCE_END = ("。", "！", "？", "；", "\n", ".", ";", "!", "?")
_BULLET = re.compile(r"^\s*([•·▪◦*\-–—]|\(?\d+[.)、]|[一二三四五六七八九十]+[、.)])")


def _clean_heading(text: str) -> str:
    cleaned = " ".join(text.split())
    match = _HEADING_NOISE.match(cleaned)
    if match and len(cleaned) - match.end() > 2:
        cleaned = cleaned[match.end():]
    return cleaned.strip()[:80]


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    """按句末优先切分长文本，保留重叠。"""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    pieces: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        if end < length:
            window = text[start:end]
            cut = max(window.rfind(mark) for mark in _SENTENCE_END)
            if cut > int(size * 0.55):
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return pieces


def _collect_context(blocks: list[Block], index: int, step: int, limit: int) -> str:
    """向一个方向收集相邻正文，最多 limit 字；遇到标题即止。"""
    parts: list[str] = []
    total = 0
    cursor = index + step
    while 0 <= cursor < len(blocks) and total < limit:
        block = blocks[cursor]
        if block.kind != "text":
            break
        if block.level:
            break
        text = " ".join(block.content.split())
        if text:
            parts.append(text)
            total += len(text)
        cursor += step
    joined = " ".join(parts).strip()
    if step < 0:  # 反向收集，恢复自然顺序
        joined = joined
    return joined[:limit]


def build_chunks(
    doc_id: str,
    filename: str,
    parsed: ParsedDocument,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
    figure_context: int | None = None,
) -> list[dict[str, Any]]:
    """把 ``ParsedDocument`` 转成待入库的 chunk 列表。"""
    size = max(200, chunk_size or settings.chunk_size)
    step_overlap = min(max(0, overlap if overlap is not None else settings.chunk_overlap), size // 2)
    fig_limit = max(60, figure_context or settings.figure_context_chars)
    media = f"/media/{doc_id}"

    blocks = parsed.blocks
    chunks: list[dict[str, Any]] = []
    buffer: list[str] = []
    buffer_pages: list[int] = []
    headings: dict[int, str] = {}

    def section_name() -> str:
        return " / ".join(headings[key] for key in sorted(headings))

    def add_chunk(chunk: dict[str, Any]) -> None:
        chunk["id"] = f"{doc_id}-{len(chunks):05d}"
        chunk["ord"] = len(chunks)
        chunks.append(chunk)

    def flush() -> None:
        if not buffer:
            return
        raw = "\n".join(buffer).strip()
        pages = [page for page in buffer_pages if page > 0] or [1]
        buffer.clear()
        buffer_pages.clear()
        if not raw:
            return
        section = section_name()
        for piece in _split_text(raw, size, step_overlap):
            add_chunk(
                {
                    "kind": "text",
                    "page": pages[0],
                    "page_end": pages[-1],
                    "section": section,
                    "content": piece,
                    "caption": "",
                    "image_path": "",
                    "image_url": "",
                    "width": 0,
                    "height": 0,
                }
            )

    for index, block in enumerate(blocks):
        if block.kind == "text":
            text = block.content.strip()
            if not text:
                continue
            if block.level and len(text) <= 90:
                flush()
                headings[block.level] = _clean_heading(text)
                for level in [key for key in headings if key > block.level]:
                    headings.pop(level, None)
                continue
            buffer.append(text)
            buffer_pages.append(block.page)
            if sum(len(item) for item in buffer) >= size * 1.6:
                flush()
            continue

        # ---------------- 图片 / 表格 ----------------
        flush()
        caption = (block.caption or "").strip()
        before = _collect_context(blocks, index, -1, fig_limit)
        after = _collect_context(blocks, index, 1, fig_limit)
        section = section_name()

        if block.kind == "image":
            lines: list[str] = []
            lines.append(f"图片说明：{caption}" if caption else "图片说明：（原文档未给出图注）")
            if section:
                lines.append(f"所在章节：{section}")
            if before:
                lines.append(f"图前正文：{before}")
            if after:
                lines.append(f"图后正文：{after}")
            if not caption and not before and not after:
                lines.append(f"《{filename}》第 {block.page} 页中的插图。")
            rel_path = block.image_path
            add_chunk(
                {
                    "kind": "figure",
                    "page": block.page,
                    "page_end": block.page,
                    "section": section,
                    "content": "\n".join(lines),
                    "caption": caption or f"第 {block.page} 页插图",
                    "image_path": rel_path,
                    "image_url": f"{media}/{rel_path}" if rel_path else "",
                    "width": int(block.meta.get("width", 0) or 0),
                    "height": int(block.meta.get("height", 0) or 0),
                    "bbox": block.bbox_json(),
                }
            )
            continue

        table_text = block.content.strip()
        if not table_text:
            continue
        table_lines = [f"表格说明：{caption}" if caption else "表格："]
        if section:
            table_lines.append(f"所在章节：{section}")
        table_lines.append(table_text)
        add_chunk(
            {
                "kind": "table",
                "page": block.page,
                "page_end": block.page,
                "section": section,
                "content": "\n".join(table_lines),
                "caption": caption,
                "image_path": "",
                "image_url": "",
                "width": 0,
                "height": 0,
                "bbox": block.bbox_json(),
            }
        )

    flush()
    return chunks
