"""Markdown 切片与图文关联：按页切分、段落合并、图片绑定到同页切片。

在链路中的位置：解析工作器拿到 `MineruResult` 后调用 `prepare()`，产出的
`ChunkDraft` / `AssetDraft` 交给 indexer 写库。切片保留页码，图片绑定到同页切片上，
让问答时“切片 → 关联图片”能直接查到（README：不单独建图片向量）。
"""

if __package__ in (None, ""):  # 允许直接运行本文件
    import sys
    from pathlib import Path

    for _root in Path(__file__).resolve().parents:
        if (_root / "backend" / "app").is_dir():
            sys.path.insert(0, str(_root))
            break

import re
from dataclasses import dataclass, field

from mineru_runner import MineruResult

PAGE_PATTERN = re.compile(r"<!--\s*page:\s*(\d+)\s*-->")
IMAGE_PATTERN = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]*)\)")
DEFAULT_MAX_CHARS = 400
DEFAULT_MAX_ASSETS_PER_CHUNK = 2


@dataclass
class AssetDraft:
    """待入库的图片资源。"""

    name: str
    data: bytes
    page: int = 1
    bbox: list[float] | None = None
    asset_type: str = "figure"
    caption: str = ""
    ocr_text: str = ""


@dataclass
class ChunkDraft:
    """待入库的文本切片；asset_indexes 指向入参 assets 列表的下标。"""

    seq: int
    page: int
    text: str
    asset_indexes: list[int] = field(default_factory=list)


def split_pages(markdown: str) -> list[tuple[int, str]]:
    """按 `<!-- page: N -->` 标记切页；没有标记时整篇算第 1 页。"""
    matches = list(PAGE_PATTERN.finditer(markdown))
    if not matches:
        return [(1, markdown)]
    pages: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        page = int(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        pages.append((page, markdown[start:end]))
    return pages


def _strip_images(text: str) -> str:
    """去掉 Markdown 图片语法：图片信息走图片资源表，正文里只保留文字。"""
    return IMAGE_PATTERN.sub("", text)


def _paragraphs(text: str) -> list[str]:
    """按空行拆段落并去首尾空白（空段直接丢掉）。"""
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def chunk_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """把一页正文合并成不超过 max_chars 的切片；过长的段落按句号硬切。"""
    chunks: list[str] = []
    buffer = ""
    for paragraph in _paragraphs(text):
        if len(paragraph) > max_chars:
            # 单段就超长：按句末标点切开，避免一个切片塞进整段（检索粒度会太粗）
            for piece in re.split(r"(?<=[。！？；])", paragraph):
                piece = piece.strip()
                if not piece:
                    continue
                if len(buffer) + len(piece) > max_chars and buffer:
                    chunks.append(buffer)
                    buffer = ""
                buffer += piece
            continue
        if len(buffer) + len(paragraph) + 1 > max_chars and buffer:
            chunks.append(buffer)
            buffer = paragraph
        else:
            buffer = f"{buffer}\n{paragraph}" if buffer else paragraph
    if buffer:
        chunks.append(buffer)
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def build_assets(result: MineruResult) -> list[AssetDraft]:
    """把 MinerU 抽取的图片转成待入库资源。"""
    return [
        AssetDraft(
            name=image.name,
            data=image.data,
            page=image.page,
            bbox=image.bbox,
            asset_type=image.asset_type,
            caption=image.caption,
        )
        for image in result.images
    ]


def associate_images(
    chunks: list[ChunkDraft],
    assets: list[AssetDraft],
    max_assets_per_chunk: int = DEFAULT_MAX_ASSETS_PER_CHUNK,
) -> None:
    """同页图片绑定到该页的切片上（图片检索走邻近文本，不单独建图片向量）。"""
    by_page: dict[int, list[int]] = {}
    for index, asset in enumerate(assets):
        by_page.setdefault(asset.page, []).append(index)
    for chunk in chunks:
        chunk.asset_indexes = by_page.get(chunk.page, [])[:max_assets_per_chunk]


def prepare(
    result: MineruResult,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[list[ChunkDraft], list[AssetDraft]]:
    """解析产物 → 切片 + 图片资源，并完成图文关联。"""
    assets = build_assets(result)
    chunks: list[ChunkDraft] = []
    for page, text in split_pages(result.markdown):
        for piece in chunk_text(_strip_images(text), max_chars=max_chars):
            chunks.append(ChunkDraft(seq=len(chunks), page=page, text=piece))
    associate_images(chunks, assets)
    return chunks, assets


def main() -> None:
    """自检：三页都切出切片、图片语法不残留、同页绑定、长度受限、无页码标记算第 1 页。"""
    from mineru_runner import MockMineruParser

    result = MockMineruParser().parse(b"%PDF-1.7\n" + b"0" * 2048, "切片测试.pdf")
    chunks, assets = prepare(result)
    print("[parse-worker.chunker] 切片数:", len(chunks), "| 图片资源数:", len(assets))
    for chunk in chunks:
        print(f"  seq={chunk.seq} page={chunk.page} assets={chunk.asset_indexes} text={chunk.text[:40]}")

    assert chunks, "应至少切出一个切片"
    assert {chunk.page for chunk in chunks} == {1, 2, 3}, "三页都应有切片"
    assert all("![系统架构图]" not in chunk.text for chunk in chunks), "图片语法不应留在正文"
    image_chunks = [chunk for chunk in chunks if chunk.asset_indexes]
    assert len(image_chunks) == 1, "只有第 2 页的切片应关联图片"
    assert image_chunks[0].page == 2 and assets[image_chunks[0].asset_indexes[0]].caption == "系统架构图"
    assert all(len(chunk.text) <= DEFAULT_MAX_CHARS for chunk in chunks), "切片长度应受限"
    first_asset = assets[0]
    assert first_asset.data.startswith(b"\x89PNG") and first_asset.bbox is not None

    # 无页码标记时整篇算第 1 页；超长段落按句号切开
    pages = split_pages("只有一段没有页码标记的正文。")
    assert pages == [(1, "只有一段没有页码标记的正文。")]
    long_text = "这是一句用于测试切片长度的句子。" * 60
    pieces = chunk_text(long_text, max_chars=100)
    print("[parse-worker.chunker] 超长段落切成", len(pieces), "段")
    assert len(pieces) > 1 and all(len(piece) <= 100 for piece in pieces)
    print("[parse-worker.chunker] 自检通过")


if __name__ == "__main__":
    main()
