"""Convert MinerU's structured content into multimodal retrieval chunks."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .textutils import (
    html_table_to_text,
    image_placeholder,
    normalize_ws,
    split_text,
    tokenize,
)

HEADING_TYPES = {"doc_title", "paragraph_title", "title", "section_title"}
TEXT_TYPES = {
    "text",
    "list",
    "abstract",
    "reference",
    "equation",
    "formula",
    "code",
    "footnote",
    "header",
    "footer",
}
IMAGE_TYPES = {"image", "figure"}


@dataclass
class ImageRef:
    rel_path: str
    page: int
    heading: str
    caption_hint: str


@dataclass
class ChunkDraft:
    kind: str
    page: int
    heading: str
    content: str
    image_path: str | None = None
    caption: str | None = None
    ordinal: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_record(self, document_id: str) -> dict[str, Any]:
        tokens = " ".join(
            tokenize(
                " ".join(
                    filter(
                        None,
                        [
                            self.heading,
                            self.content,
                            self.caption,
                            " ".join(self.extra.get("keywords", [])),
                        ],
                    )
                )
            )
        )
        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"mmrag:{document_id}:{self.ordinal}")),
            "ordinal": self.ordinal,
            "kind": self.kind,
            "page": self.page,
            "heading": self.heading,
            "content": self.content,
            "image_path": self.image_path,
            "caption": self.caption,
            "tokens": tokens,
        }


def load_structured(parsed_dir: Path) -> dict[str, Any]:
    path = parsed_dir / "structured_content.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    middle = parsed_dir / "middle_json.json"
    if middle.exists():
        return json.loads(middle.read_text(encoding="utf-8"))
    return {"pages": []}


def document_title(structured: dict[str, Any], fallback: str) -> str:
    for page in structured.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("type") == "doc_title":
                text = normalize_ws(str(block.get("content") or ""))
                if text:
                    return text[:180]
    return Path(fallback).stem


def page_count(structured: dict[str, Any]) -> int:
    metadata = structured.get("metadata", {})
    count = (
        metadata.get("document", {}).get("page_count")
        if isinstance(metadata, dict)
        else None
    )
    if isinstance(count, int) and count > 0:
        return count
    pages = structured.get("pages", [])
    return max((int(page.get("page_idx", 0)) + 1 for page in pages), default=0)


def safe_media_rel(raw: str, parsed_dir: Path) -> str | None:
    """Validate and normalise an image path coming from MinerU."""
    if not raw:
        return None
    path = Path(str(raw).replace("\\", "/"))
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(parsed_dir.resolve())
        except ValueError:
            return None
    candidate = (parsed_dir / path).resolve()
    try:
        rel = candidate.relative_to(parsed_dir.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return rel.as_posix()


def collect_image_refs(
    structured: dict[str, Any], parsed_dir: Path
) -> list[ImageRef]:
    """Collect figure images that deserve a VLM caption."""
    refs: list[ImageRef] = []
    seen: set[str] = set()
    heading = ""
    for page in structured.get("pages", []):
        page_no = int(page.get("page_idx", 0)) + 1
        for block in page.get("blocks", []):
            block_type = str(block.get("type", ""))
            text = normalize_ws(str(block.get("content") or ""))
            if block_type in HEADING_TYPES and text:
                heading = text[:160]
            if block_type not in IMAGE_TYPES:
                continue
            rel = safe_media_rel(str(block.get("image_source") or ""), parsed_dir)
            if not rel or rel in seen:
                continue
            seen.add(rel)
            captions = block.get("captions") or []
            caption_hint = " ".join(
                normalize_ws(str(caption)) for caption in captions if caption
            ).strip()
            refs.append(
                ImageRef(
                    rel_path=rel,
                    page=page_no,
                    heading=heading,
                    caption_hint=caption_hint,
                )
            )
    return refs


def _media_record(
    raw: str | None, parsed_dir: Path, document_id: str
) -> tuple[str | None, str | None]:
    rel = safe_media_rel(raw or "", parsed_dir)
    if not rel:
        return None, None
    return f"{document_id}/{rel}", rel


def build_chunks(
    *,
    document_id: str,
    filename: str,
    structured: dict[str, Any],
    parsed_dir: Path,
    captions: dict[str, str] | None = None,
    fallback_pages: list[str] | None = None,
) -> tuple[str, int, list[dict[str, Any]]]:
    """Build citations-ready text/table/image chunks.

    Returns (title, page_count, records).
    """
    captions = captions or {}
    title = document_title(structured, filename)
    total_pages = page_count(structured)
    drafts: list[ChunkDraft] = []

    current_heading = ""
    buffer = ""

    def flush(page_no: int) -> None:
        nonlocal buffer
        text = normalize_ws(buffer)
        buffer = ""
        if not text:
            return
        for piece in split_text(text):
            content = f"{current_heading}\n{piece}" if current_heading else piece
            if len(content.strip()) < 8:
                continue
            drafts.append(
                ChunkDraft(
                    kind="text",
                    page=page_no,
                    heading=current_heading,
                    content=content.strip(),
                    extra={"keywords": [title, filename]},
                )
            )

    for page in structured.get("pages", []):
        page_no = int(page.get("page_idx", 0)) + 1
        for block in page.get("blocks", []):
            block_type = str(block.get("type", ""))
            text = normalize_ws(str(block.get("content") or ""))
            if block_type in HEADING_TYPES:
                flush(page_no)
                if text:
                    current_heading = text[:180]
                continue

            if block_type in IMAGE_TYPES:
                flush(page_no)
                image_path, rel = _media_record(
                    str(block.get("image_source") or ""), parsed_dir, document_id
                )
                if not image_path or not rel:
                    continue
                caption = normalize_ws(
                    captions.get(rel)
                    or " ".join(
                        normalize_ws(str(item))
                        for item in (block.get("captions") or [])
                        if item
                    )
                )
                caption = image_placeholder(caption)
                descriptive = (
                    f"图片描述：{caption}"
                    + (f"\n所属章节：{current_heading}" if current_heading else "")
                )
                drafts.append(
                    ChunkDraft(
                        kind="image",
                        page=page_no,
                        heading=current_heading,
                        content=descriptive,
                        image_path=image_path,
                        caption=caption,
                        extra={"keywords": [title, filename, "插图", "图片"]},
                    )
                )
                continue

            if block_type == "table":
                flush(page_no)
                table_text = html_table_to_text(text)
                captions_text = " ".join(
                    normalize_ws(str(item))
                    for item in (block.get("captions") or [])
                    if item
                )
                body = table_text or captions_text or "表格（未识别出可读文字）"
                image_path, _ = _media_record(
                    str(block.get("image_source") or ""), parsed_dir, document_id
                )
                descriptive = f"表格内容：{body}"
                if current_heading:
                    descriptive = f"所属章节：{current_heading}\n{descriptive}"
                drafts.append(
                    ChunkDraft(
                        kind="table",
                        page=page_no,
                        heading=current_heading,
                        content=descriptive,
                        image_path=image_path,
                        caption=captions_text or None,
                        extra={"keywords": [title, filename, "表格"]},
                    )
                )
                continue

            if block_type in TEXT_TYPES and text:
                buffer += ("\n" if buffer else "") + text
                if len(buffer) >= 850:
                    flush(page_no)
        flush(page_no)

    # Some PDFs (for example a full-page screenshot) contain useful embedded
    # text but no MinerU text blocks. pypdf gives us a graceful fallback.
    meaningful_text = [
        draft for draft in drafts if draft.kind in {"text", "table"} and len(draft.content) > 80
    ]
    if len(meaningful_text) < 2 and fallback_pages:
        existing = {normalize_ws(draft.content) for draft in drafts if draft.kind == "text"}
        for index, page_text in enumerate(fallback_pages, start=1):
            page_text = normalize_ws(page_text)
            if not page_text or page_text in existing:
                continue
            for piece in split_text(page_text):
                if len(piece) < 30:
                    continue
                drafts.append(
                    ChunkDraft(
                        kind="text",
                        page=min(index, total_pages or index),
                        heading="PDF 文本层",
                        content=piece,
                        extra={"keywords": [title, filename, "PDF 文本"]},
                    )
                )
                total_pages = max(total_pages, index)

    # Preserve reading order but group text/table ahead of isolated figures.
    drafts.sort(key=lambda draft: (draft.page, 0 if draft.kind != "image" else 1))
    records: list[dict[str, Any]] = []
    for ordinal, draft in enumerate(drafts):
        draft.ordinal = ordinal
        records.append(draft.as_record(document_id))

    return title, max(total_pages, 1), records
